"""Tests for the AI helper.

Offline tests (always run): what is sent to the AI, and that its answer is only a suggestion.
Live tests (run with RUN_AI_TESTS=1 and GEMINI_API_KEY set): ask the real model and check
its suggestions against the right answers.
"""
import json
import os

import pytest
from fastapi.testclient import TestClient

from tests import dummy_data as dd

import api.index  # noqa: F401  (reads .env so the live tests can find the key)
from biota import ai
from biota.analysis import CHANNELS, STATUS_GROUP_LABELS

LIVE = os.environ.get("RUN_AI_TESTS") == "1" and bool(os.environ.get("GEMINI_API_KEY"))
live = pytest.mark.skipif(not LIVE, reason="set RUN_AI_TESTS=1 (and GEMINI_API_KEY) to call the real AI")
live_slow = [live, pytest.mark.usefixtures("gentle")]


@pytest.fixture
def client(tmp_path, monkeypatch):
    from api.index import app
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ai.db"))
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    return TestClient(app)


# ---------- offline ----------

def test_only_names_and_labels_are_sent_never_order_rows(client, monkeypatch):
    sent = []
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(ai, "_ask", lambda prompt: sent.append(prompt) or {"columns": {}, "channels": {}, "statuses": {}})
    truth = dd.build_truth()
    w1 = dd.week1_excel(truth)
    client.post("/api/upload", files={"file": ("w1.xlsx", w1)})
    client.post("/api/upload", files={"file": ("w1.xlsx", w1)},
                data={"mapping": json.dumps({"columns": {"order_date": "Tgl Transaksi"}})})
    assert len(sent) == 2
    prompts = "\n".join(sent)
    for ln in truth:
        assert ln.order_id not in prompts
        assert ln.city not in prompts
        assert f"{ln.total:,}".replace(",", ".") not in prompts and str(ln.total) not in prompts
    assert "Tgl Transaksi" in prompts and "Menunggu Pickup" in prompts


def test_ai_suggestion_is_never_applied_without_confirmation(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(ai, "_ask", lambda prompt: {
        "columns": {"order_date": "Tgl Transaksi"},
        "channels": {"Tokopedia Official": "Tokopedia"},
        "statuses": {"Menunggu Pickup": "shipping", "Pesanan Dibatalkan Pembeli": "cancelled"},
    })
    w1 = dd.week1_excel(dd.build_truth())
    r = client.post("/api/upload", files={"file": ("w1.xlsx", w1)})
    assert r.status_code == 409
    q = r.json()["questions"]
    assert q["ai_used"] is True and q["columns"][0]["suggestion"] == "Tgl Transaksi"
    # Nothing was stored just because the AI had an answer.
    assert client.get("/api/dashboard").json()["empty"] is True


def test_busy_ai_is_retried(monkeypatch):
    import urllib.error
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(ai, "RETRY_WAITS", (0, 0))
    monkeypatch.setattr(ai, "_cache", {})
    calls = []
    def flaky(prompt):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError("u", 503, "busy", {}, None)
        return {"columns": {}, "channels": {"X": "Shopee"}, "statuses": {}}
    monkeypatch.setattr(ai, "_call", flaky)
    q = ai.suggest({"columns": [], "headers": [], "channels": [{"value": "X", "suggestion": None}], "statuses": []})
    assert len(calls) == 3 and q["ai_used"] is True and q["channels"][0]["suggestion"] == "Shopee"


def test_quota_limit_is_explained(monkeypatch):
    import urllib.error
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(ai, "RETRY_WAITS", (0, 0))
    monkeypatch.setattr(ai, "_cache", {})
    def limited(prompt):
        raise urllib.error.HTTPError("u", 429, "quota", {}, None)
    monkeypatch.setattr(ai, "_call", limited)
    q = ai.suggest({"columns": [], "headers": [], "channels": [{"value": "X", "suggestion": None}], "statuses": []})
    assert q["ai_used"] is False and "batas pemakaian" in q["ai_error"]


@pytest.fixture(autouse=False)
def gentle():
    """Free tier allows only a few calls per minute: wait between live tests."""
    import time
    yield
    time.sleep(8)


# ---------- live (real Gemini) ----------

def ask(columns=(), headers=(), channels=(), statuses=()):
    from biota.analysis import FIELDS
    return ai.suggest({
        "columns": [{"field": f, "label": FIELDS[f], "suggestion": None} for f in columns],
        "headers": list(headers),
        "channels": [{"value": v, "suggestion": None} for v in channels],
        "statuses": [{"value": v, "suggestion": None} for v in statuses],
    })


@pytest.mark.usefixtures("gentle")
@live
def test_live_week1_questions():
    q = ask(columns=["order_date"], headers=list(dd.WEEK1_HEADERS.values()),
            channels=["Tokopedia Official"], statuses=["Menunggu Pickup", "Pesanan Dibatalkan Pembeli"])
    assert q["ai_used"] is True
    assert q["columns"][0]["suggestion"] == "Tgl Transaksi"
    assert q["channels"][0]["suggestion"] == "Tokopedia"
    s = {x["value"]: x["suggestion"] for x in q["statuses"]}
    assert s["Pesanan Dibatalkan Pembeli"] == "cancelled"
    assert s["Menunggu Pickup"] in ("shipping", None)  # never "completed"


@pytest.mark.usefixtures("gentle")
@live
def test_live_english_export_headers():
    headers = ["Order ID", "Created Time", "Platform Name", "Seller SKU", "Product Name", "Quantity",
               "SKU Unit Original Price", "SKU Subtotal After Discount", "Order Status", "Buyer City"]
    q = ask(columns=["order_date", "channel", "total_price"], headers=headers)
    got = {c["field"]: c["suggestion"] for c in q["columns"]}
    assert got == {"order_date": "Created Time", "channel": "Platform Name", "total_price": "SKU Subtotal After Discount"}


@pytest.mark.usefixtures("gentle")
@live
def test_live_tricky_statuses():
    labels = {
        "Belum Bayar": {None},                    # unpaid: must not count as sold
        "Perlu Dikirim": {"shipping", None},       # paid, waiting to ship
        "Delivered": {"completed"},
        "Pesanan Diterima": {"completed"},
        "Return/Refund": {"returned"},
        "Dibatalkan Sistem": {"cancelled"},
        "Gagal Kirim": {"cancelled", "returned", None},
    }
    q = ask(statuses=list(labels))
    for x in q["statuses"]:
        assert x["suggestion"] in labels[x["value"]], (x["value"], x["suggestion"])


@pytest.mark.usefixtures("gentle")
@live
def test_live_channel_variants():
    q = ask(channels=["shopee mall", "TikTokShop ID", "Tokped Official Store", "Lazada", "Blibli"])
    got = {c["value"]: c["suggestion"] for c in q["channels"]}
    assert got["shopee mall"] == "Shopee"
    assert got["TikTokShop ID"] == "TikTok Shop"
    assert got["Tokped Official Store"] == "Tokopedia"
    assert got["Lazada"] is None and got["Blibli"] is None  # not one of our three channels


@pytest.mark.usefixtures("gentle")
@live
def test_live_answers_stay_inside_the_allowed_options():
    q = ask(columns=["order_date", "status"], headers=["foo", "bar"], channels=["???"], statuses=["xyz"])
    for c in q["columns"]:
        assert c["suggestion"] in (None, "foo", "bar")
    assert q["channels"][0]["suggestion"] in (None, *CHANNELS)
    assert q["statuses"][0]["suggestion"] in (None, *STATUS_GROUP_LABELS)
