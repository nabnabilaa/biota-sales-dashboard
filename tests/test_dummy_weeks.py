"""Two new, messy weekly exports through the real API, compared with independently computed answers."""
import json

import pytest
from fastapi.testclient import TestClient

from tests import dummy_data as dd

ANSWERS = {
    "columns": {"order_date": "Tgl Transaksi"},
    "channels": {"Tokopedia Official": "Tokopedia"},
    "statuses": {"Menunggu Pickup": "shipping", "Pesanan Dibatalkan Pembeli": "cancelled"},
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from api.index import app
    monkeypatch.setenv("DB_PATH", str(tmp_path / "dummy.db"))
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return TestClient(app)


def post(client, name, content, mapping=None):
    data = {"mapping": json.dumps(mapping)} if mapping else {}
    return client.post("/api/upload", files={"file": (name, content)}, data=data)


def test_two_weekly_exports_give_the_right_numbers(client):
    truth = dd.build_truth()
    w1, w2 = dd.week1_excel(truth), dd.week2_csv(truth)

    # Week 1: the odd column name, the new channel spelling and the two new status labels
    # are all asked on one screen.
    r = post(client, "minggu1.xlsx", w1)
    assert r.status_code == 409
    q = r.json()["questions"]
    assert [c["field"] for c in q["columns"]] == ["order_date"]
    assert "Tgl Transaksi" in q["headers"]
    assert [c["value"] for c in q["channels"]] == ["Tokopedia Official"]
    assert sorted(s["value"] for s in q["statuses"]) == ["Menunggu Pickup", "Pesanan Dibatalkan Pembeli"]

    r = post(client, "minggu1.xlsx", w1, ANSWERS)
    assert r.status_code == 200, r.text
    up = r.json()["upload"]
    exp1 = dd.week1_expectations(truth)
    assert up["rows_in_file"] == exp1["rows"]
    assert len(up["rows_dropped"]) == exp1["dropped"]  # broken date + new product without SKU
    assert up["added"] == exp1["stored"]
    reasons = " ".join(d["reason"] for d in up["rows_dropped"])
    assert "kemarin" in reasons and "BIOTA Smart Camera Mini" in reasons

    # Week 2 overlaps week 1: no questions again, statuses updated, nothing counted twice.
    r = post(client, "minggu2.csv", w2)
    assert r.status_code == 200, r.text
    body = r.json()
    up = body["upload"]
    assert up["added"] + up["updated"] + up["unchanged"] == len(truth)
    assert up["duplicates_removed"] > 0
    assert up["updated"] > 0

    exp = dd.expected(truth)
    s = body["summary"]
    assert s["net_revenue"] == exp["net_revenue"]
    assert s["orders"] == exp["orders"]
    assert s["units"] == exp["units"]
    assert {c["channel"]: c["revenue"] for c in s["by_channel"]} == exp["by_channel"]
    assert {p["sku"]: (p["revenue"], p["units"]) for p in s["products"]} == exp["by_product"]
    lost = s["not_counted"]
    assert lost["cancelled"]["value"] + lost["returned"]["value"] == exp["lost_value"]
    assert body["stored_rows"] == len(truth)

    # A date filter on the merged data.
    first = client.get("/api/dashboard", params={"start": "2026-11-01", "end": "2026-11-07"}).json()
    assert first["summary"]["net_revenue"] == dd.expected(truth, 1, 7)["net_revenue"]

    # The new product shows up once its SKU is known.
    assert "BIO-CAM-MINI" in {p["sku"] for p in s["products"]}


def test_uploading_week_2_again_changes_nothing(client):
    truth = dd.build_truth()
    post(client, "minggu1.xlsx", dd.week1_excel(truth), ANSWERS)
    post(client, "minggu2.csv", dd.week2_csv(truth))
    before = client.get("/api/dashboard").json()["summary"]["net_revenue"]
    up = post(client, "minggu2-lagi.csv", dd.week2_csv(truth)).json()["upload"]
    assert (up["added"], up["updated"]) == (0, 0)
    assert client.get("/api/dashboard").json()["summary"]["net_revenue"] == before


def test_different_random_weeks(client):
    """Same checks on other generated data, so the test is not tuned to one file."""
    for seed in (11, 23):
        client.delete("/api/data")
        truth = dd.build_truth(seed)
        assert post(client, "w1.xlsx", dd.week1_excel(truth), ANSWERS).status_code == 200
        body = post(client, "w2.csv", dd.week2_csv(truth, seed)).json()
        exp = dd.expected(truth)
        assert body["summary"]["net_revenue"] == exp["net_revenue"]
        assert {c["channel"]: c["revenue"] for c in body["summary"]["by_channel"]} == exp["by_channel"]
