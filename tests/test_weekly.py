"""Weekly uploads add to the stored data; answers and the team password work as expected."""
import pytest
from fastapi.testclient import TestClient

HEADER = "order_id,order_date,channel,sku,product_name,qty,unit_price,total_price,status,buyer_city"


def csv(*lines: str) -> bytes:
    return "\n".join([HEADER, *lines]).encode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from api.index import app  # importing reads .env; the settings below override it
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # tests never call the real AI
    return TestClient(app)


def upload(client, content: bytes, name="orders.csv", mapping=None):
    data = {"mapping": mapping} if mapping else {}
    return client.post("/api/upload", files={"file": (name, content, "text/csv")}, data=data)


def test_empty_at_start(client):
    assert client.get("/api/dashboard").json()["empty"] is True


def test_second_week_adds_new_orders_and_updates_statuses(client):
    week1 = csv(
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Dikirim,Bogor",
        "A2,2026-10-02 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
    )
    first = upload(client, week1).json()["upload"]
    assert (first["added"], first["updated"], first["unchanged"]) == (2, 0, 0)

    # Next week's export overlaps: A1 is now delivered, then cancelled? no: A1 completed,
    # A2 is unchanged, A3 is new.
    week2 = csv(
        "A1,2026-10-01 10:00,SHOPEE,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A2,2026-10-02 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A3,2026-10-08 10:00,Shopee,BIO-X,Kamera X,2,Rp100.000,Rp200.000,Selesai,Bogor",
    )
    res = upload(client, week2).json()
    assert (res["upload"]["added"], res["upload"]["updated"], res["upload"]["unchanged"]) == (1, 1, 1)
    assert res["summary"]["net_revenue"] == 400000
    assert res["summary"]["shipping_revenue"] == 0  # A1 no longer counted as "in delivery"
    assert res["stored_rows"] == 3
    assert [u["filename"] for u in res["uploads"]] == ["orders.csv", "orders.csv"]


def test_a_return_reported_later_removes_the_revenue(client):
    upload(client, csv("A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Dikirim,Bogor"))
    res = upload(client, csv("A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Pengembalian Dana,Bogor")).json()
    assert res["summary"]["net_revenue"] == 0


def test_same_file_twice_changes_nothing(client):
    content = csv("A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor")
    upload(client, content)
    res = upload(client, content).json()
    assert (res["upload"]["added"], res["upload"]["unchanged"]) == (0, 1)
    assert res["summary"]["net_revenue"] == 100000


def test_answers_are_remembered(client):
    content = csv("A1,2026-10-01 10:00,Lazada,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Menunggu Pickup,Bogor")
    assert upload(client, content).status_code == 409
    answer = '{"channels": {"Lazada": "Shopee"}, "statuses": {"Menunggu Pickup": "shipping"}}'
    assert upload(client, content, mapping=answer).status_code == 200
    # Next week: same labels, no questions.
    next_week = csv("A9,2026-10-09 10:00,Lazada,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Menunggu Pickup,Bogor")
    assert upload(client, next_week).status_code == 200


def test_blank_sku_filled_from_an_earlier_upload(client):
    upload(client, csv("A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor"))
    res = upload(client, csv("A2,2026-10-08 10:00,Shopee,,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor")).json()
    assert res["upload"]["sku_filled"] == 1
    assert res["summary"]["products"][0]["units"] == 2


def test_team_password(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "rahasia-tim")
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/session").json() == {"password_required": True, "logged_in": False}
    assert client.post("/api/login", data={"password": "salah"}).status_code == 401
    assert client.post("/api/login", data={"password": "rahasia-tim"}).status_code == 200
    assert client.get("/api/dashboard").status_code == 200


def test_data_files_are_not_served(client):
    assert client.get("/data/biota_orders_oct2026.csv").status_code == 404
    assert client.get("/storage/biota.db").status_code == 404


def test_order_lines_behind_a_number(client):
    upload(client, csv(
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A2,2026-10-01 11:00,Tokopedia,BIO-X,Kamera X,2,200000,200000,Batal,Depok",
        "A3,2026-10-02 10:00,Shopee,BIO-Y,Lampu Y,1,Rp50.000,Rp50.000,Selesai,Bogor",
    ))
    day = client.get("/api/orders", params={"sku": "BIO-X", "date": "2026-10-01", "status": "counted"}).json()
    assert [o["order_id"] for o in day["orders"]] == ["A1"] and day["total"] == 100000
    lost = client.get("/api/orders", params={"status": "lost"}).json()
    assert [o["order_id"] for o in lost["orders"]] == ["A2"]
    ch = client.get("/api/orders", params={"channel": "Shopee", "start": "2026-10-02", "end": "2026-10-02"}).json()
    assert [o["order_id"] for o in ch["orders"]] == ["A3"]


def test_orders_by_city(client):
    upload(client, csv(
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A2,2026-10-01 11:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Depok",
    ))
    res = client.get("/api/orders", params={"sku": "BIO-X", "city": "Depok"}).json()
    assert [o["order_id"] for o in res["orders"]] == ["A2"]


def test_table_filters(client):
    upload(client, csv(
        "A1,2026-10-01 10:00,Shopee,BIO-CAM-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A2,2026-10-05 10:00,Tokopedia,BIO-PET-Y,Feeder Y,1,300000,300000,Selesai,Depok",
        "A3,2026-10-09 10:00,Shopee,BIO-PET-Y,Feeder Y,1,Rp300.000,Rp300.000,Batal,Depok",
    ))
    pet = client.get("/api/products", params={"category": "Perlengkapan hewan"}).json()
    assert [p["sku"] for p in pet["products"]] == ["BIO-PET-Y"] and pet["net_revenue"] == 300000
    early = client.get("/api/products", params={"start": "2026-10-01", "end": "2026-10-03"}).json()
    assert [p["sku"] for p in early["products"]] == ["BIO-CAM-X"]
    shopee = client.get("/api/products", params={"channel": "Shopee"}).json()
    assert shopee["net_revenue"] == 100000  # the Shopee feeder order was cancelled
    lost_pet = client.get("/api/orders", params={"category": "Perlengkapan hewan", "status": "lost"}).json()
    assert [o["order_id"] for o in lost_pet["orders"]] == ["A3"] and lost_pet["orders"][0]["category"] == "Perlengkapan hewan"


# ---------- edge cases: changed quantity, missing orders, genuinely identical lines ----------

def test_changed_quantity_updates_the_line_instead_of_adding_one(client):
    upload(client, csv("A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,2,Rp100.000,Rp200.000,Dikirim,Bogor"))
    # One of the two units was cancelled: qty 2 -> 1, total 200k -> 100k.
    res = upload(client, csv("A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor")).json()
    assert (res["upload"]["added"], res["upload"]["updated"]) == (0, 1)
    assert res["summary"]["net_revenue"] == 100000 and res["summary"]["units"] == 1
    assert res["stored_rows"] == 1


def test_two_lines_of_the_same_product_in_one_order_are_kept_and_updated(client):
    first = csv(
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,2,Rp100.000,Rp200.000,Selesai,Bogor",
    )
    assert upload(client, first).json()["summary"]["net_revenue"] == 300000
    second = csv(
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,3,Rp100.000,Rp300.000,Selesai,Bogor",
    )
    res = upload(client, second).json()
    assert (res["upload"]["added"], res["upload"]["updated"], res["upload"]["unchanged"]) == (0, 1, 1)
    assert res["summary"]["net_revenue"] == 400000


def test_orders_missing_from_a_newer_export_are_shown_not_removed(client):
    upload(client, csv(
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A2,2026-10-02 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A3,2026-10-20 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",  # outside next file's dates
        "B1,2026-10-02 10:00,Tokopedia,BIO-X,Kamera X,1,100000,100000,Selesai,Bogor",      # other channel
    ))
    res = upload(client, csv(
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A9,2026-10-03 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
    )).json()
    assert [m["order_id"] for m in res["missing"]] == ["A2"]
    assert res["summary"]["net_revenue"] == 500000  # nothing removed yet
    after = client.post("/api/remove-orders", json={"order_ids": ["A2"]}).json()
    assert after["removed_lines"] == 1 and after["summary"]["net_revenue"] == 400000
    assert "Hapus 1 pesanan" in after["uploads"][0]["filename"]


def test_unreadable_rows_do_not_count_as_missing(client):
    upload(client, csv("A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
                       "A2,2026-10-02 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor"))
    res = upload(client, csv("A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
                             "A2,kemarin,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor")).json()
    assert res["missing"] == []


def test_line_id_column_keeps_identical_lines_apart(client):
    header = HEADER + ",Order Item ID"
    content = "\n".join([
        header,
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor,L1",
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor,L2",  # a real second line
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor,L2",  # exported twice
    ]).encode()
    res = upload(client, content).json()
    assert res["summary"]["net_revenue"] == 200000
    assert res["upload"]["duplicates_removed"] == 1 and res["upload"]["has_line_id"] is True


def test_without_line_id_identical_lines_are_listed_for_checking(client):
    res = upload(client, csv(
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
        "A1,2026-10-01 10:00,Shopee,BIO-X,Kamera X,1,Rp100.000,Rp100.000,Selesai,Bogor",
    )).json()
    assert res["upload"]["duplicate_lines"] == [{"line": 3, "order_id": "A1"}]


def test_old_line_keys_are_moved_to_the_new_format(tmp_path, monkeypatch):
    import sqlite3
    from biota import storage
    db = tmp_path / "old.db"
    monkeypatch.setenv("DB_PATH", str(db))
    con = sqlite3.connect(db)
    con.executescript(storage.SCHEMA.replace("CREATE TABLE IF NOT EXISTS meta (\n    key   TEXT PRIMARY KEY,\n    value TEXT NOT NULL\n);", ""))
    con.execute("INSERT INTO orders VALUES ('A1|BIO-X|1|100000','A1','BIO-X','2026-10-01','Shopee','Kamera X',1,100000,'completed','Bogor',0,1,'t')")
    con.commit(); con.close()
    with storage.connect() as c:
        assert [k for (k,) in c.execute("SELECT line_key FROM orders")] == ["A1|BIO-X|1"]
