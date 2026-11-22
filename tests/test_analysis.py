from pathlib import Path

import pandas as pd
import pytest

from biota.analysis import DataError, NeedsMapping, analyze, parse_dates, parse_rupiah

HEADER = "order_id,order_date,channel,sku,product_name,qty,unit_price,total_price,status,buyer_city"


def csv(*lines: str) -> str:
    return "\n".join([HEADER, *lines])


def test_every_price_format():
    values = ["Rp1.099.000", "Rp 1.099.000", "1,099,000", "IDR 349,000", "1099000", ""]
    assert parse_rupiah(pd.Series(values)).tolist() == [1099000, 1099000, 1099000, 349000, 1099000, pd.NA]


def test_tiktok_dates_are_day_first():
    days = parse_dates(pd.Series(["2026-10-21 08:24", "02/10/2026 11:09:00", "14/10/2026 23:26:00", "Oct 04, 2026", "kemarin"]))
    assert [d.strftime("%Y-%m-%d") if not pd.isna(d) else None for d in days] == [
        "2026-10-21", "2026-10-02", "2026-10-14", "2026-10-04", None,
    ]  # 02/10 is 2 October, not 10 February


def test_channel_and_status_spellings_are_merged():
    result = analyze(csv(
        "A1,2026-10-01 10:00,shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Pesanan Selesai,Bogor",
        "A2,02/10/2026 10:00:00,TIKTOK SHOP,BIO-X,Kamera X,1,\"100,000\",\"100,000\",In transit,Bogor",
        "A3,\"Oct 03, 2026\",Tokped,BIO-X,Kamera X,1,100000,100000,Completed,Bogor",
    ))
    assert [c["revenue"] for c in result["summary"]["by_channel"]] == [100000, 100000, 100000]


def test_duplicates_removed_even_when_spelled_differently():
    result = analyze(csv(
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A1,2026-10-01 10:00,SHOPEE,BIO-X,Kamera X,1,Rp 100.000,Rp100.000,Selesai,Bogor",
    ))
    assert result["report"]["duplicates_removed"] == 1
    assert result["summary"]["net_revenue"] == 100000


def test_blank_sku_filled_from_listing_name():
    result = analyze(csv(
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A2,2026-10-02 10:00,Shopee,,Kamera X,2,Rp100.000,Rp200.000,Selesai,Bogor",
    ))
    assert result["report"]["sku_filled"] == 1
    assert result["summary"]["products"] == [{"sku": "BIO-X", "product": "Kamera X", "category": "Lainnya", "revenue": 300000, "units": 3}]


def test_cancelled_and_returned_are_not_revenue():
    result = analyze(csv(
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A2,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Dikirim,Bogor",
        "A3,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Batal,Bogor",
        "A4,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Pengembalian Dana,Bogor",
    ))
    s = result["summary"]
    assert s["net_revenue"] == 200000
    assert s["not_counted"] == {"cancelled": {"orders": 1, "value": 100000}, "returned": {"orders": 1, "value": 100000}}


def test_date_filter_includes_both_ends():
    text = csv(
        "A1,2026-10-01 00:05,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A2,15/10/2026 23:59:00,TikTok Shop,BIO-X,Kamera X,1,100000,100000,Completed,Bogor",
        "A3,\"Oct 16, 2026\",Tokopedia,BIO-X,Kamera X,1,100000,100000,Selesai,Bogor",
    )
    assert analyze(text, "2026-10-01", "2026-10-15")["summary"]["net_revenue"] == 200000


def test_unreadable_rows_are_reported_not_counted():
    result = analyze(csv(
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A2,kemarin,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
    ))
    assert result["report"]["rows_dropped"] == [{"line": 3, "reason": 'tanggal tidak terbaca: "kemarin"'}]
    assert result["summary"]["net_revenue"] == 100000


def test_wrong_file_gives_clear_error():
    with pytest.raises(DataError):
        analyze(b"not an excel file", filename="foto.xlsx")


def test_other_column_names_are_recognised():
    text = "\n".join([
        "No. Pesanan,Waktu Pesanan Dibuat,Marketplace,SKU Induk,Nama Produk,Jumlah,Harga Satuan,Total Harga,Status Pesanan,Kota",
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,2,Rp100.000,Rp200.000,Selesai,Bogor",
    ])
    assert analyze(text)["summary"]["net_revenue"] == 200000


def test_excel_file_is_read(tmp_path):
    df = pd.DataFrame([{
        "order_id": "A1", "order_date": pd.Timestamp("2026-10-02 09:00"), "channel": "Tokopedia",
        "sku": "BIO-X", "product_name": "Kamera X", "qty": 1, "unit_price": 1099000,
        "total_price": 1099000, "status": "Selesai", "buyer_city": "Bogor",
    }])
    path = tmp_path / "orders.xlsx"
    df.to_excel(path, index=False)
    result = analyze(path.read_bytes(), filename="orders.xlsx")
    assert result["summary"]["net_revenue"] == 1099000  # not 10990000 from "1099000.0"
    assert result["period"]["first_day"] == "2026-10-02"


def test_unknown_column_asks_the_user_then_uses_the_answer():
    text = "\n".join([
        "order_id,Tgl Transaksi,channel,sku,product_name,qty,unit_price,total_price,status,buyer_city",
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
    ])
    with pytest.raises(NeedsMapping) as asked:
        analyze(text)
    assert [q["field"] for q in asked.value.questions["columns"]] == ["order_date"]
    result = analyze(text, mapping={"columns": {"order_date": "Tgl Transaksi"}})
    assert result["summary"]["net_revenue"] == 100000


def test_unknown_channel_and_status_ask_the_user():
    text = csv(
        "A1,2026-10-01 10:00,Lazada,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A2,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Menunggu Pickup,Bogor",
    )
    with pytest.raises(NeedsMapping) as asked:
        analyze(text)
    q = asked.value.questions
    assert [c["value"] for c in q["channels"]] == ["Lazada"]
    assert [s["value"] for s in q["statuses"]] == ["Menunggu Pickup"]
    # Answers outside the allowed options are ignored, so the question comes back.
    with pytest.raises(NeedsMapping):
        analyze(text, mapping={"channels": {"Lazada": "Bukalapak"}, "statuses": {"Menunggu Pickup": "shipping"}})
    result = analyze(text, mapping={"channels": {"Lazada": "Shopee"}, "statuses": {"Menunggu Pickup": "shipping"}})
    assert result["summary"]["net_revenue"] == 200000


def test_ai_suggestions_outside_the_options_are_dropped(monkeypatch):
    from biota import ai
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(ai, "_ask", lambda prompt: {
        "columns": {"order_date": "Tgl Transaksi"}, "channels": {"Lazada": "Lazada"}, "statuses": {"Menunggu": "shipping"},
    })
    q = ai.suggest({
        "columns": [{"field": "order_date", "label": "", "suggestion": None}], "headers": ["Tgl Transaksi"],
        "channels": [{"value": "Lazada", "suggestion": None}], "statuses": [{"value": "Menunggu", "suggestion": None}],
    })
    assert q["columns"][0]["suggestion"] == "Tgl Transaksi"
    assert q["channels"][0]["suggestion"] is None  # not one of the three channels
    assert q["statuses"][0]["suggestion"] == "shipping"


def test_ai_failure_does_not_break_the_app(monkeypatch):
    from biota import ai
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    def boom(prompt):
        raise TimeoutError
    monkeypatch.setattr(ai, "_ask", boom)
    q = ai.suggest({"columns": [], "headers": [], "channels": [{"value": "X", "suggestion": None}], "statuses": []})
    assert q["ai_used"] is False and q["channels"][0]["suggestion"] is None


# The final numbers in the README, checked earlier with a separate script.
# The data file is confidential and not in the repo: put it in data/ to run this test.
DATA = Path(__file__).resolve().parent.parent / "data" / "biota_orders_oct2026.csv"


@pytest.mark.skipif(not DATA.exists(), reason="data file not present")
def test_october_2026_export_gives_readme_numbers():
    result = analyze(DATA.read_bytes())
    report, s = result["report"], result["summary"]
    assert (report["rows_in_file"], report["duplicates_removed"], report["sku_filled"]) == (495, 22, 76)
    assert report["rows_dropped"] == []
    assert (report["channel_spellings"], report["status_labels"]) == (10, 12)
    assert s["net_revenue"] == 298_937_000
    assert [c["revenue"] for c in s["by_channel"]] == [180_315_000, 70_461_000, 48_161_000]
    assert (s["orders"], s["units"]) == (366, 473)
    assert sum(d["revenue"] for d in s["by_day"]) == s["net_revenue"] and len(s["by_day"]) == 31
    assert analyze(DATA.read_bytes(), "2026-10-01", "2026-10-15")["summary"]["net_revenue"] == 146_226_000


@pytest.mark.skipif(not DATA.exists(), reason="data file not present")
def test_october_findings():
    f = analyze(DATA.read_bytes())["findings"]
    assert f["gross"] == 298_937_000 + f["lost"]["value"]
    assert (f["lost"]["value"], f["lost"]["orders"]) == (61_161_000, 74)
    assert f["lost"]["by_product"][0]["name"] == "BIOTA Lumi Hexagon Light Panel"
    assert f["lost"]["by_product"][0]["key"] == "BIO-LGT-HEX"
    assert (f["stuck_shipping"]["orders"], f["stuck_shipping"]["value"]) == (38, 29_684_000)
    assert f["camera_without_card"] == {"camera_orders": 218, "with_card": 7}
    assert (f["best_day"]["date"], f["best_day"]["revenue"]) == ("2026-10-04", 21_910_000)
    assert sum(len(v) for v in f["listings_without_sku"].values()) == 26


@pytest.mark.skipif(not DATA.exists(), reason="data file not present")
def test_details_add_up_to_the_totals():
    result = analyze(DATA.read_bytes(), "2026-10-01", "2026-10-15")
    s, d = result["summary"], result["details"]
    assert sum(p["revenue"] for p in d["products"].values()) == s["net_revenue"]
    assert sum(c["revenue"] for c in d["channels"].values()) == s["net_revenue"]
    for p in s["products"]:
        one = d["products"][p["sku"]]
        assert (one["revenue"], one["units"]) == (p["revenue"], p["units"])
        assert sum(c["revenue"] for c in one["by_channel"]) == one["revenue"]
        assert sum(one["by_day"]) == one["revenue"] and len(one["by_day"]) == 15
    assert len(d["products"]["BIO-CAM-BF"]["listings"]) == 3  # one listing name per marketplace


@pytest.mark.skipif(not DATA.exists(), reason="data file not present")
def test_day_details_match_the_daily_chart():
    result = analyze(DATA.read_bytes())
    days = result["details"]["days"]
    for d in result["summary"]["by_day"]:
        one = days[d["date"]]
        assert one["revenue"] == d["revenue"]
        assert sum(c["revenue"] for c in one["by_channel"]) == d["revenue"]
    assert days["2026-10-04"]["revenue"] == 21_910_000
