"""Fake weekly exports with known right answers.

Builds the "truth" first (every order with its final status), then writes two messy
exports from it the way a team would pull them:

  week 1 (Excel, pulled on 7 Nov): orders of 1-7 Nov with the status they had then,
      Indonesian column names, one odd column name, a new channel spelling, two new
      status labels, a broken date, and a new product whose SKU is still blank.
  week 2 (CSV, pulled on 14 Nov): orders of 1-14 Nov with their final status, so it
      overlaps week 1, plus a few lines exported twice.

expected() computes the right numbers straight from the truth, without any of the
app's code, so the test compares the app against an independent answer.
"""
from __future__ import annotations

import io
import random
from dataclasses import dataclass, field

import pandas as pd

PRODUCTS = {
    # sku: (price, {channel: listing name})
    "BIO-CAM-BF": (1_099_000, {"Shopee": "BIOTA Smart Camera Battery Fixed - CCTV Wifi Tanpa Kabel",
                               "TikTok Shop": "BIOTA Smart Home - Kamera Battery Fixed CCTV Wifi",
                               "Tokopedia": "Kamera CCTV Battery Fixed BIOTA"}),
    "BIO-PET-FD": (899_000, {"Shopee": "BIOTA Smart Pet Feeder - Dispenser Makanan Otomatis",
                             "TikTok Shop": "BIOTA Smart Pet Feeder dengan Kamera",
                             "Tokopedia": "Pet Feeder Otomatis BIOTA"}),
    "BIO-SD-S64": (119_000, {"Shopee": "BIOTA MicroSD SONIC Series 64GB",
                             "TikTok Shop": "BIOTA Sonic MicroSD - Memory Card 64GB",
                             "Tokopedia": "Memory Card BIOTA Sonic 64GB"}),
    "BIO-LGT-HEX": (1_499_000, {"Shopee": "BIOTA Lumi Hexagon Light Panel",
                                "TikTok Shop": "BIOTA Lumi Hexagon - Lampu Panel RGB",
                                "Tokopedia": "Lampu Hexagon BIOTA Lumi"}),
    # New this month: never seen in October.
    "BIO-CAM-MINI": (299_000, {"Shopee": "BIOTA Smart Camera Mini", "TikTok Shop": "BIOTA Smart Camera Mini",
                               "Tokopedia": "BIOTA Smart Camera Mini"}),
}
CHANNELS = ["Shopee", "TikTok Shop", "Tokopedia"]
CITIES = ["Jakarta Barat", "Bogor", "Depok", "Bandung", "Surabaya", "Medan"]
PREFIX = {"Shopee": "SHP", "TikTok Shop": "TTS", "Tokopedia": "TKP"}

# How each marketplace writes each status (week 2 uses the usual words).
LABEL = {
    "Shopee": {"completed": "Selesai", "shipping": "Dikirim", "cancelled": "Batal", "returned": "Pengembalian Dana"},
    "TikTok Shop": {"completed": "Completed", "shipping": "In transit", "cancelled": "Cancelled", "returned": "Refunded"},
    "Tokopedia": {"completed": "Pesanan Selesai", "shipping": "Sedang Dikirim", "cancelled": "Dibatalkan", "returned": "Dikomplain - Retur"},
}
COUNTED = {"completed", "shipping"}

WEEK1_HEADERS = {  # Indonesian column names; "Tgl Transaksi" is not in the app's list on purpose
    "order_id": "No. Pesanan", "order_date": "Tgl Transaksi", "channel": "Marketplace", "sku": "SKU Induk",
    "product_name": "Nama Produk", "qty": "Jumlah", "unit_price": "Harga Satuan", "total_price": "Total Harga",
    "status": "Status Pesanan", "buyer_city": "Kota",
}


@dataclass
class Line:
    order_id: str
    day: int            # November day
    channel: str
    sku: str
    qty: int
    city: str
    final: str          # final status group (week 2)
    week1: str = ""     # status group or new label when pulled on 7 Nov
    flags: set = field(default_factory=set)

    @property
    def total(self) -> int:
        return PRODUCTS[self.sku][0] * self.qty


def build_truth(seed: int = 7) -> list[Line]:
    rnd = random.Random(seed)
    lines: list[Line] = []
    for n in range(120):
        channel = rnd.choices(CHANNELS, weights=[6, 3, 2])[0]
        day = rnd.randint(1, 14)
        order_id = f"{PREFIX[channel]}NOV{n:04d}"
        final = rnd.choices(["completed", "shipping", "cancelled", "returned"], weights=[70, 12, 12, 6])[0]
        city = rnd.choice(CITIES)
        skus = rnd.sample(list(PRODUCTS), k=rnd.choice([1, 1, 1, 2]))
        for sku in skus:
            lines.append(Line(order_id, day, channel, sku, rnd.choice([1, 1, 2, 3]), city, final))

    # Make sure every case the test needs exists, whatever the seed.
    def ensure(channel: str, final: str) -> None:
        if any(ln.channel == channel and ln.day <= 7 and ln.final == final for ln in lines):
            return
        target = next(ln.order_id for ln in lines if ln.channel == channel and ln.day <= 7)
        for ln in lines:
            if ln.order_id == target:
                ln.final = final

    ensure("Shopee", "completed")
    ensure("TikTok Shop", "cancelled")

    # What week 1 saw on 7 Nov for orders placed 1-7 Nov.
    for ln in lines:
        if ln.day > 7:
            continue
        if ln.final == "completed" and ln.day >= 5:
            ln.week1 = "shipping"                          # delivered later
        elif ln.final == "returned":
            ln.week1 = "completed"                         # returned after delivery
        else:
            ln.week1 = ln.final
    # New labels in week 1 (one order each, all its lines).
    shp_wait = next(ln for ln in lines if ln.channel == "Shopee" and ln.day <= 7 and ln.final == "completed")
    tts_cancel = next(ln for ln in lines if ln.channel == "TikTok Shop" and ln.day <= 7 and ln.final == "cancelled")
    for ln in lines:
        if ln.order_id == shp_wait.order_id:
            ln.week1 = "LABEL:Menunggu Pickup"
        if ln.order_id == tts_cancel.order_id:
            ln.week1 = "LABEL:Pesanan Dibatalkan Pembeli"
    # One Tokopedia order written "Tokopedia Official" in week 1.
    tkp = next(ln for ln in lines if ln.channel == "Tokopedia" and ln.day <= 7)
    for ln in lines:
        if ln.order_id == tkp.order_id:
            ln.flags.add("channel_official")
    # A broken date in week 1 (fixed in week 2).
    next(ln for ln in lines if ln.day <= 7 and "channel_official" not in ln.flags).flags.add("bad_date")
    # The new product has no SKU in week 1 yet.
    for ln in lines:
        if ln.sku == "BIO-CAM-MINI":
            ln.flags.add("blank_sku_week1")
    return lines


def _date_text(channel: str, day: int) -> str:
    if channel == "Shopee":
        return f"2026-11-{day:02d} 10:15"
    if channel == "TikTok Shop":
        return f"{day:02d}/11/2026 10:15:00"
    return f"Nov {day:02d}, 2026"


def _price_text(channel: str, value: int) -> str:
    if channel == "Shopee":
        return "Rp" + f"{value:,}".replace(",", ".")
    if channel == "TikTok Shop":
        return f"{value:,}"
    return str(value)


def week1_excel(lines: list[Line]) -> bytes:
    rows = []
    for ln in lines:
        if ln.day > 7:
            continue
        status = ln.week1[6:] if ln.week1.startswith("LABEL:") else LABEL[ln.channel][ln.week1]
        rows.append({
            WEEK1_HEADERS["order_id"]: ln.order_id,
            WEEK1_HEADERS["order_date"]: "kemarin" if "bad_date" in ln.flags else _date_text(ln.channel, ln.day),
            WEEK1_HEADERS["channel"]: "Tokopedia Official" if "channel_official" in ln.flags else ln.channel,
            WEEK1_HEADERS["sku"]: "" if "blank_sku_week1" in ln.flags else ln.sku,
            WEEK1_HEADERS["product_name"]: PRODUCTS[ln.sku][1][ln.channel],
            WEEK1_HEADERS["qty"]: ln.qty,
            WEEK1_HEADERS["unit_price"]: PRODUCTS[ln.sku][0],
            WEEK1_HEADERS["total_price"]: ln.total,
            WEEK1_HEADERS["status"]: status,
            WEEK1_HEADERS["buyer_city"]: ln.city,
        })
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False)
    return buf.getvalue()


def week2_csv(lines: list[Line], seed: int = 7) -> bytes:
    rnd = random.Random(seed + 1)
    rows = []
    for ln in lines:
        row = [ln.order_id, _date_text(ln.channel, ln.day), ln.channel.upper() if rnd.random() < 0.1 else ln.channel,
               ln.sku, PRODUCTS[ln.sku][1][ln.channel], str(ln.qty), _price_text(ln.channel, PRODUCTS[ln.sku][0]),
               _price_text(ln.channel, ln.total), LABEL[ln.channel][ln.final], ln.city]
        rows.append(row)
        if rnd.random() < 0.05:
            rows.append(list(row))  # exported twice
    header = "order_id,order_date,channel,sku,product_name,qty,unit_price,total_price,status,buyer_city"
    quote = lambda v: f'"{v}"' if "," in v else v
    return ("\n".join([header] + [",".join(quote(v) for v in r) for r in rows]) + "\n").encode()


def expected(lines: list[Line], start: int = 1, end: int = 14) -> dict:
    sel = [ln for ln in lines if start <= ln.day <= end]
    counted = [ln for ln in sel if ln.final in COUNTED]
    lost = [ln for ln in sel if ln.final not in COUNTED]
    by_channel = {ch: sum(ln.total for ln in counted if ln.channel == ch) for ch in CHANNELS}
    by_product = {}
    for ln in counted:
        rev, units = by_product.get(ln.sku, (0, 0))
        by_product[ln.sku] = (rev + ln.total, units + ln.qty)
    return {
        "net_revenue": sum(ln.total for ln in counted),
        "orders": len({ln.order_id for ln in counted}),
        "units": sum(ln.qty for ln in counted),
        "by_channel": by_channel,
        "by_product": by_product,
        "lost_value": sum(ln.total for ln in lost),
        "lost_orders": len({ln.order_id for ln in lost}),
    }


def week1_expectations(lines: list[Line]) -> dict:
    """What the week 1 upload alone should give once the questions are answered."""
    w1 = [ln for ln in lines if ln.day <= 7]
    dropped = [ln for ln in w1 if "bad_date" in ln.flags or "blank_sku_week1" in ln.flags]
    return {"rows": len(w1), "dropped": len(dropped), "stored": len(w1) - len(dropped)}


if __name__ == "__main__":  # writes the two files for a manual try in the browser
    import pathlib
    out = pathlib.Path(__file__).resolve().parent / "dummy_out"
    out.mkdir(exist_ok=True)
    t = build_truth()
    (out / "export_minggu1_1-7nov.xlsx").write_bytes(week1_excel(t))
    (out / "export_minggu2_1-14nov.csv").write_bytes(week2_csv(t))
    print(expected(t))
