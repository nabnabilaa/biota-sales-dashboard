"""All data logic: read the file, clean it, and calculate the numbers (pandas).

No web code and no AI here, so the same functions run in the API and in the tests,
and the numbers never depend on an AI answer.
"""
from __future__ import annotations

import io
import re

import pandas as pd

# ---------- what we expect in the file ----------

FIELDS = {
    "order_id": "Nomor pesanan",
    "order_date": "Tanggal pesanan",
    "channel": "Channel / marketplace",
    "sku": "Kode produk (SKU)",
    "product_name": "Nama produk",
    "qty": "Jumlah unit",
    "unit_price": "Harga per unit",
    "total_price": "Total harga baris",
    "status": "Status pesanan",
    "buyer_city": "Kota pembeli",
    "line_id": "ID baris pesanan (opsional)",
}
REQUIRED = ["order_id", "order_date", "channel", "sku", "product_name", "qty", "total_price", "status"]

# Other names the same column may carry in a marketplace export. Compared without
# case, spaces or punctuation, so "No. Pesanan" and "no_pesanan" both match.
COLUMN_ALIASES = {
    "order_id": ["orderid", "nopesanan", "nomorpesanan", "noorder", "orderno", "ordernumber", "idpesanan", "invoice", "noinvoice", "nomorinvoice"],
    "order_date": ["orderdate", "tanggal", "tanggalpesanan", "tglpesanan", "waktupesanan", "waktupesanandibuat", "createdtime", "ordercreatedtime", "createdat", "date"],
    "channel": ["channel", "marketplace", "platform", "saluran", "channelpenjualan", "sumber"],
    "sku": ["sku", "sellersku", "skuinduk", "nomorreferensisku", "kodeproduk", "kodesku"],
    "product_name": ["productname", "namaproduk", "produk", "product", "itemname", "namabarang"],
    "qty": ["qty", "quantity", "jumlah", "jumlahproduk", "kuantitas", "jumlahbarang"],
    "unit_price": ["unitprice", "harga", "hargasatuan", "hargaproduk", "price", "hargaperunit"],
    "total_price": ["totalprice", "total", "subtotal", "totalharga", "totalhargaproduk", "jumlahharga"],
    "status": ["status", "statuspesanan", "orderstatus"],
    "buyer_city": ["buyercity", "kota", "kotapembeli", "city", "kotakabupaten"],
    # Marketplace exports often have an ID per order line. When present, two identical
    # lines with different IDs are two real lines, not a line exported twice.
    "line_id": ["lineid", "orderitemid", "lineitemid", "orderlineid", "orderitemsid", "idbarispesanan"],
}

CHANNELS = ["Shopee", "TikTok Shop", "Tokopedia"]
CHANNEL_ALIASES = {
    "shopee": "Shopee",
    "tiktok shop": "TikTok Shop",
    "tiktok": "TikTok Shop",
    "tokopedia": "Tokopedia",
    "tokped": "Tokopedia",
}

# Each marketplace reports status in its own words. We fold them into four groups.
STATUS_GROUP_LABELS = {
    "completed": "Selesai",
    "shipping": "Dalam pengiriman",
    "cancelled": "Batal",
    "returned": "Retur / dana dikembalikan",
}
STATUS_ALIASES = {
    "selesai": "completed",
    "pesanan selesai": "completed",
    "completed": "completed",
    "dikirim": "shipping",
    "sedang dikirim": "shipping",
    "in transit": "shipping",
    "batal": "cancelled",
    "dibatalkan": "cancelled",
    "cancelled": "cancelled",
    "pengembalian dana": "returned",
    "refunded": "returned",
    "dikomplain - retur": "returned",
}

# Product category, read from the internal SKU prefix.
CATEGORIES = [
    ("BIO-CAM", "Kamera CCTV"),
    ("BIO-PET", "Perlengkapan hewan"),
    ("BIO-SD", "Kartu memori"),
    ("BIO-LGT", "Lampu"),
]


def category_of(sku: str) -> str:
    for prefix, label in CATEGORIES:
        if str(sku).startswith(prefix):
            return label
    return "Lainnya"


# Net revenue counts finished and on-the-way orders; cancelled and returned do not count.
COUNTS_AS_REVENUE = {"completed", "shipping"}

# Shopee "2026-10-21 08:24", TikTok Shop "14/10/2026 23:26:00" (day first), Tokopedia "Oct 04, 2026".
DATE_FORMATS = [
    "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y",
    "%b %d, %Y", "%d %b %Y",
]


class DataError(ValueError):
    """The file cannot be used at all (not a CSV/Excel file, or empty)."""


class NeedsMapping(Exception):
    """Some columns, channels or statuses are not recognised. The user has to say what they are."""

    def __init__(self, questions: dict):
        super().__init__("Ada kolom atau label yang belum dikenali.")
        self.questions = questions


# ---------- small helpers ----------

def _label(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def parse_rupiah(series: pd.Series) -> pd.Series:
    """'Rp1.099.000', 'Rp 1.099.000', '1,099,000', 'IDR 349,000', '1099000' -> 1099000.

    Rupiah in these exports has no cents, so every '.' and ',' is a thousands separator.
    The one exception is how Excel stores whole numbers ('1099000.0'), which is cut first.
    """
    text = series.astype(str).str.strip().str.replace(r"^(\d+)\.0+$", r"\1", regex=True)
    digits = text.str.replace(r"\D", "", regex=True)
    return pd.to_numeric(digits.where(digits != ""), errors="coerce").astype("Int64")


def parse_dates(series: pd.Series) -> pd.Series:
    """Returns the calendar day of each order. Tries each known format in turn."""
    text = series.astype(str).str.strip()
    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    for fmt in DATE_FORMATS:
        missing = result.isna()
        if not missing.any():
            break
        result[missing] = pd.to_datetime(text[missing], format=fmt, errors="coerce")
    return result.dt.normalize()


# ---------- reading ----------

def read_table(content: bytes | str, filename: str = "data.csv") -> pd.DataFrame:
    """Reads a CSV or Excel file into text columns, exactly as written in the file."""
    try:
        if re.search(r"\.xlsx?$", filename, re.I):
            raw = pd.read_excel(io.BytesIO(content), dtype=str).fillna("")
        else:
            if isinstance(content, bytes):
                content = content.decode("utf-8-sig", errors="replace")
            raw = pd.read_csv(io.StringIO(content), dtype=str, keep_default_na=False, skip_blank_lines=True)
    except Exception as exc:  # pandas raises many types for a broken file
        raise DataError("File tidak bisa dibuka. Pastikan formatnya CSV atau Excel (.xlsx).") from exc
    raw.columns = [str(c).strip() for c in raw.columns]
    if raw.empty or len(raw.columns) < 2:
        raise DataError("File tidak berisi baris pesanan.")
    return raw.apply(lambda col: col.astype(str).str.strip())


def match_columns(headers: list[str], chosen: dict | None = None) -> tuple[dict, list[str]]:
    """Finds which file column holds each field. Returns {field: header} and the fields still missing."""
    chosen = {k: v for k, v in (chosen or {}).items() if k in FIELDS and v in headers}
    by_key = {_header(h): h for h in headers}
    found = dict(chosen)
    for field, aliases in COLUMN_ALIASES.items():
        if field in found:
            continue
        for alias in [field.replace("_", ""), *aliases]:
            if alias in by_key and by_key[alias] not in found.values():
                found[field] = by_key[alias]
                break
    missing = [f for f in REQUIRED if f not in found]
    return found, missing


def unknown_labels(values: pd.Series, known: dict, chosen: dict) -> list[str]:
    seen = {}
    for v in values:
        if v and _label(v) not in known and v not in chosen:
            seen.setdefault(v, None)
    return list(seen)


def prepare(raw: pd.DataFrame, mapping: dict | None = None) -> pd.DataFrame:
    """Renames columns to our field names and turns every channel/status into one known value.

    Raises NeedsMapping with the questions for the user when something is not recognised.
    """
    mapping = mapping or {}
    headers = list(raw.columns)
    found, missing = match_columns(headers, mapping.get("columns"))

    chosen_channels = {k: v for k, v in mapping.get("channels", {}).items() if v in CHANNELS}
    chosen_status = {k: v for k, v in mapping.get("statuses", {}).items() if v in STATUS_GROUP_LABELS}
    # Labels are checked in every column already found, so all questions come on one screen.
    new_channels = unknown_labels(raw[found["channel"]], CHANNEL_ALIASES, chosen_channels) if "channel" in found else []
    new_statuses = unknown_labels(raw[found["status"]], STATUS_ALIASES, chosen_status) if "status" in found else []
    if missing or new_channels or new_statuses:
        raise NeedsMapping({
            "columns": [{"field": f, "label": FIELDS[f], "suggestion": None} for f in missing],
            "headers": headers,
            "channels": [{"value": v, "suggestion": None} for v in new_channels],
            "statuses": [{"value": v, "suggestion": None} for v in new_statuses],
        })

    df = pd.DataFrame({field: raw[header] for field, header in found.items()})
    for field in FIELDS:
        if field not in df:
            df[field] = ""

    df["channel_raw"] = df["channel"]  # kept so the report can count the original spellings
    df["channel"] = [chosen_channels.get(v) or CHANNEL_ALIASES.get(_label(v)) for v in df["channel"]]
    df["status_group"] = [chosen_status.get(v) or STATUS_ALIASES.get(_label(v)) for v in df["status"]]
    df.attrs["columns_used"] = found
    return df


# ---------- cleaning ----------

def name_products(df: pd.DataFrame) -> pd.DataFrame:
    """Name shown on the dashboard: the most common listing name for each SKU."""
    if df.empty:
        df["product"] = ""
        return df
    names = (
        df.groupby(["sku", "listing_name"]).size().reset_index(name="n")
        .sort_values(["sku", "n", "listing_name"], ascending=[True, False, True])
        .drop_duplicates("sku").set_index("sku")["listing_name"]
    )
    df["product"] = df["sku"].map(names)
    return df


def clean(df: pd.DataFrame, known_names: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Turns prepared rows into clean rows, plus a report of what was changed or dropped.

    known_names: listing name -> SKU learned from earlier uploads, so a blank SKU can be
    filled even when this week's file has no row that carries it.
    """
    out = pd.DataFrame({
        "line": df.index + 2,  # line number in the file (header is line 1)
        "order_id": df["order_id"],
        "date": parse_dates(df["order_date"]),
        "channel": df["channel"],
        "sku": df["sku"],
        "listing_name": df["product_name"],
        "qty": pd.to_numeric(df["qty"], errors="coerce"),
        "total": parse_rupiah(df["total_price"]),
        "status": df["status_group"],
        "city": df["buyer_city"],
        "line_id": df["line_id"],
    })

    # The same product is listed under a different name on each channel. Rows that carry
    # a SKU teach us which listing name belongs to which SKU; blank SKUs are filled by name.
    with_sku = out[out["sku"] != ""]
    name_to_sku = {**(known_names or {}), **dict(zip(with_sku["listing_name"].map(_label), with_sku["sku"]))}
    blank = out["sku"] == ""
    out["sku_was_blank"] = blank
    out.loc[blank, "sku"] = out.loc[blank, "listing_name"].map(_label).map(name_to_sku).fillna("")
    sku_filled = int((blank & (out["sku"] != "")).sum())

    # Rows we cannot read are reported, never silently counted.
    problems = pd.Series("", index=out.index)
    checks = [
        (out["order_id"] == "", lambda r: "nomor pesanan kosong"),
        (out["date"].isna(), lambda r: f'tanggal tidak terbaca: "{df.at[r.name, "order_date"]}"'),
        (out["sku"] == "", lambda r: f'produk tidak dikenali: "{r.listing_name}"'),
        (~(out["qty"] > 0) | (out["qty"] % 1 != 0), lambda r: f'jumlah tidak valid: "{df.at[r.name, "qty"]}"'),
        (out["total"].isna(), lambda r: "total harga kosong"),
    ]
    for mask, message in checks:
        todo = mask & (problems == "")
        if todo.any():
            problems[todo] = out[todo].apply(message, axis=1)
    bad = problems != ""
    dropped = [{"line": int(out.at[i, "line"]), "reason": problems[i]} for i in out.index[bad]]
    out = out[~bad].copy()

    # The same line exported twice. Compared after cleaning, so "Shopee" vs "SHOPEE" or
    # "Rp 1.099.000" vs "Rp1.099.000" do not hide a duplicate. With a line ID column, only
    # lines that share the ID are duplicates.
    dup = out.duplicated(subset=["order_id", "line_id", "sku", "qty", "total", "status", "date"], keep="first")
    duplicates = int(dup.sum())
    duplicate_lines = [{"line": int(r.line), "order_id": r.order_id} for r in out[dup].itertuples()]
    out = out[~dup].copy()

    out["qty"] = out["qty"].astype(int)
    out["total"] = out["total"].astype(int)

    out = name_products(out)

    report = {
        "rows_in_file": int(len(df)),
        "duplicates_removed": duplicates,
        "duplicate_lines": duplicate_lines[:50],
        "has_line_id": bool((df["line_id"] != "").any()),
        "sku_filled": sku_filled,
        "rows_dropped": dropped,
        "rows_used": int(len(out)),
        "channel_spellings": int(df["channel_raw"].nunique()),
        "status_labels": int(df["status"].nunique()),
    }
    return out.drop(columns=["line"]).reset_index(drop=True), report


# ---------- numbers ----------

def filter_dates(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    """Keeps orders from start to end, both days included. Dates are 'YYYY-MM-DD'."""
    mask = pd.Series(True, index=df.index)
    if start:
        mask &= df["date"] >= pd.Timestamp(start)
    if end:
        mask &= df["date"] <= pd.Timestamp(end)
    return df[mask]


def summarize(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    counted = df[df["status"].isin(COUNTS_AS_REVENUE)]
    excluded = df[~df["status"].isin(COUNTS_AS_REVENUE)]

    by_channel = counted.groupby("channel").agg(revenue=("total", "sum"), orders=("order_id", "nunique"))
    by_channel = by_channel.reindex(CHANNELS, fill_value=0)

    products = (
        counted.groupby(["sku", "product"], as_index=False)
        .agg(revenue=("total", "sum"), units=("qty", "sum"))
        .sort_values(["revenue", "sku"], ascending=[False, True])
    )

    days = pd.date_range(start, end, freq="D")
    by_day = counted.groupby("date")["total"].sum().reindex(days, fill_value=0)

    excluded_by_group = excluded.groupby("status").agg(orders=("order_id", "nunique"), value=("total", "sum"))

    def group(name: str) -> dict:
        if name in excluded_by_group.index:
            row = excluded_by_group.loc[name]
            return {"orders": int(row["orders"]), "value": int(row["value"])}
        return {"orders": 0, "value": 0}

    return {
        "net_revenue": int(counted["total"].sum()),
        "orders": int(counted["order_id"].nunique()),
        "units": int(counted["qty"].sum()),
        "shipping_revenue": int(counted.loc[counted["status"] == "shipping", "total"].sum()),
        "by_channel": [
            {"channel": ch, "revenue": int(r.revenue), "orders": int(r.orders)} for ch, r in by_channel.iterrows()
        ],
        "by_day": [{"date": d.strftime("%Y-%m-%d"), "revenue": int(v)} for d, v in by_day.items()],
        "products": [
            {"sku": r.sku, "product": r.product, "category": category_of(r.sku), "revenue": int(r.revenue), "units": int(r.units)}
            for r in products.itertuples()
        ],
        "categories": sorted({category_of(x) for x in df["sku"].unique()}),
        "not_counted": {"cancelled": group("cancelled"), "returned": group("returned")},
    }


def detail(rows: pd.DataFrame, total_revenue: int, days: pd.DatetimeIndex) -> dict:
    """Everything shown when one product or one channel is clicked."""
    counted = rows[rows["status"].isin(COUNTS_AS_REVENUE)]
    revenue = int(counted["total"].sum())
    gross = int(rows["total"].sum())
    cancelled = rows[rows["status"] == "cancelled"]
    returned = rows[rows["status"] == "returned"]
    by_day = counted.groupby("date")["total"].sum().reindex(days, fill_value=0)
    cities = counted.groupby("city")["total"].sum().sort_values(ascending=False)
    cities = cities[cities.index != ""].head(5)
    return {
        "revenue": revenue,
        "orders": int(counted["order_id"].nunique()),
        "units": int(counted["qty"].sum()),
        "share": round(revenue / total_revenue, 4) if total_revenue else 0,
        "avg_order": int(revenue / counted["order_id"].nunique()) if not counted.empty else 0,
        "by_day": [int(v) for v in by_day.values],
        "lost": {
            "gross": gross,
            "cancelled": {"orders": int(cancelled["order_id"].nunique()), "value": int(cancelled["total"].sum())},
            "returned": {"orders": int(returned["order_id"].nunique()), "value": int(returned["total"].sum())},
        },
        "cities": [{"city": c, "revenue": int(v)} for c, v in cities.items()],
    }


def details(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """Detail for every product (by SKU) and every channel in the selected period."""
    total = int(df.loc[df["status"].isin(COUNTS_AS_REVENUE), "total"].sum())
    days = pd.date_range(start, end, freq="D")
    products = {}
    for sku, rows in df.groupby("sku"):
        d = detail(rows, total, days)
        counted = rows[rows["status"].isin(COUNTS_AS_REVENUE)]
        ch = counted.groupby("channel").agg(revenue=("total", "sum"), units=("qty", "sum")).reindex(CHANNELS, fill_value=0)
        d["by_channel"] = [{"channel": c, "revenue": int(r.revenue), "units": int(r.units)} for c, r in ch.iterrows()]
        # The listing name this product carries on each marketplace.
        d["listings"] = {c: sorted(g["listing_name"].unique().tolist()) for c, g in rows.groupby("channel")}
        products[sku] = d
    channels = {}
    for channel in CHANNELS:
        rows = df[df["channel"] == channel]
        d = detail(rows, total, days)
        counted = rows[rows["status"].isin(COUNTS_AS_REVENUE)]
        top = (counted.groupby(["sku", "product"], as_index=False)
               .agg(revenue=("total", "sum"), units=("qty", "sum"))
               .sort_values(["revenue", "sku"], ascending=[False, True]).head(5))
        d["top_products"] = [{"sku": r.sku, "product": r.product, "revenue": int(r.revenue), "units": int(r.units)} for r in top.itertuples()]
        channels[channel] = d
    by_date = {}
    for date in days:
        rows = df[df["date"] == date]
        d = detail(rows, total, pd.DatetimeIndex([date]))
        counted = rows[rows["status"].isin(COUNTS_AS_REVENUE)]
        ch = counted.groupby("channel").agg(revenue=("total", "sum"), orders=("order_id", "nunique")).reindex(CHANNELS, fill_value=0)
        d["by_channel"] = [{"channel": c, "revenue": int(r.revenue), "orders": int(r.orders)} for c, r in ch.iterrows()]
        top = (counted.groupby(["sku", "product"], as_index=False)
               .agg(revenue=("total", "sum"), units=("qty", "sum"))
               .sort_values(["revenue", "sku"], ascending=[False, True]).head(5))
        d["top_products"] = [{"sku": r.sku, "product": r.product, "revenue": int(r.revenue), "units": int(r.units)} for r in top.itertuples()]
        by_date[date.strftime("%Y-%m-%d")] = d
    return {"products": products, "channels": channels, "days": by_date}


STUCK_DAYS = 7  # an order still "in delivery" this long after it was placed needs a check


def findings(df: pd.DataFrame, data_end: pd.Timestamp, blank_sku_listings: dict) -> dict:
    """Problems behind the totals that the business should act on.

    Plain numbers only; the dashboard turns them into sentences.
    """
    counted = df[df["status"].isin(COUNTS_AS_REVENUE)]
    lost = df[df["status"].isin({"cancelled", "returned"})]
    gross = int(df["total"].sum())

    # 1. Money lost to cancellations and returns, per channel and per product.
    def lost_share(group: str) -> list[dict]:
        g = df.groupby(group).agg(gross=("total", "sum"))
        g["cancelled"] = df[df["status"] == "cancelled"].groupby(group)["total"].sum()
        g["returned"] = df[df["status"] == "returned"].groupby(group)["total"].sum()
        g = g.fillna(0)
        g["share"] = (g["cancelled"] + g["returned"]) / g["gross"]
        g = g[g["gross"] > 0].sort_values("share", ascending=False)
        names = df.drop_duplicates("sku").set_index("sku")["product"] if group == "sku" else None
        return [
            {"name": str(names[k]) if names is not None else str(k), "key": str(k),
             "gross": int(r.gross), "cancelled": int(r.cancelled), "returned": int(r.returned), "share": round(float(r.share), 4)}
            for k, r in g.iterrows()
        ]

    # 2. Orders still "in delivery" long after they were placed.
    shipping = df[df["status"] == "shipping"]
    stuck = shipping[shipping["date"] <= data_end - pd.Timedelta(days=STUCK_DAYS)]
    oldest = stuck["date"].min()

    # 3. Cameras sold without a memory card (a CCTV needs one to record).
    baskets = counted.groupby("order_id")["sku"].apply(set) if not counted.empty else pd.Series(dtype=object)
    has_cam = pd.Series([any(x.startswith("BIO-CAM") for x in s) for s in baskets], dtype=bool)
    has_sd = pd.Series([any(x.startswith("BIO-SD") for x in s) for s in baskets], dtype=bool)

    # 4. Best day.
    by_day = counted.groupby("date")["total"].sum()
    best_day = by_day.idxmax() if not by_day.empty else None
    best_by_channel = (
        counted[counted["date"] == best_day].groupby("channel")["total"].sum().sort_values(ascending=False)
        if best_day is not None else pd.Series(dtype=int)
    )

    return {
        "gross": gross,
        "lost": {
            "value": int(lost["total"].sum()),
            "orders": int(lost["order_id"].nunique()),
            "share": round(float(lost["total"].sum() / gross), 4) if gross else 0,
            "by_channel": lost_share("channel"),
            "by_product": lost_share("sku"),
        },
        "stuck_shipping": {
            "days": STUCK_DAYS,
            "orders": int(stuck["order_id"].nunique()),
            "value": int(stuck["total"].sum()),
            "oldest": oldest.strftime("%Y-%m-%d") if not pd.isna(oldest) else None,
            "by_channel": {k: int(v) for k, v in stuck.groupby("channel")["order_id"].nunique().items()},
        },
        "camera_without_card": {
            "camera_orders": int(has_cam.sum()),
            "with_card": int((has_cam & has_sd).sum()),
        },
        "best_day": {
            "date": best_day.strftime("%Y-%m-%d") if best_day is not None else None,
            "revenue": int(by_day.max()) if not by_day.empty else 0,
            "median": int(by_day.median()) if not by_day.empty else 0,
            "top_channel": best_by_channel.index[0] if not best_by_channel.empty else None,
        },
        "listings_without_sku": blank_sku_listings,
    }


def ingest(content: bytes | str, filename: str = "data.csv", mapping: dict | None = None,
           known_names: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Reads and cleans one uploaded file. Raises NeedsMapping / DataError."""
    prepared = prepare(read_table(content, filename), mapping)
    df, report = clean(prepared, known_names)
    if df.empty:
        raise DataError("Tidak ada baris yang bisa dibaca dari file ini.")
    report["columns_used"] = prepared.attrs["columns_used"]
    # Every order number in the file, readable or not: used to spot stored orders that are
    # no longer in a newer export. Not saved with the upload.
    report["_order_ids"] = sorted(set(prepared["order_id"]) - {""})
    return df, report


def build(df: pd.DataFrame, start: str | None = None, end: str | None = None, report: dict | None = None) -> dict:
    """Everything the dashboard shows, from clean rows (one file or all stored uploads)."""
    df = name_products(df.copy())
    first, last = df["date"].min(), df["date"].max()
    s = pd.Timestamp(start) if start else first
    e = pd.Timestamp(end) if end else last
    blank = df[df["sku_was_blank"].astype(bool)]
    blank_listings = {ch: sorted(g["listing_name"].unique().tolist()) for ch, g in blank.groupby("channel")}
    selected = filter_dates(df, start, end)
    return {
        "findings": findings(selected, last, blank_listings),
        "period": {
            "first_day": first.strftime("%Y-%m-%d"),
            "last_day": last.strftime("%Y-%m-%d"),
            "start": s.strftime("%Y-%m-%d"),
            "end": e.strftime("%Y-%m-%d"),
        },
        "summary": summarize(selected, s, e),
        "details": details(selected, s, e),
        "report": report or {},
    }


def analyze(content: bytes | str, start: str | None = None, end: str | None = None,
            filename: str = "data.csv", mapping: dict | None = None) -> dict:
    """One file in, dashboard out, nothing stored. Used by the tests."""
    df, report = ingest(content, filename, mapping)
    return build(df, start, end, report)


ORDER_FILTERS = {
    "counted": COUNTS_AS_REVENUE,
    "lost": {"cancelled", "returned"},
    "cancelled": {"cancelled"},
    "returned": {"returned"},
    "shipping": {"shipping"},
    "completed": {"completed"},
}


def order_lines(df: pd.DataFrame, start: str | None = None, end: str | None = None, sku: str | None = None,
                channel: str | None = None, date: str | None = None, status: str | None = None,
                city: str | None = None, category: str | None = None) -> dict:
    """The individual order lines behind any number on the dashboard, newest first.

    status: counted | lost | cancelled | returned | shipping | completed | stuck
    ("stuck" = still in delivery STUCK_DAYS days before the last day in the data).
    """
    data_end = df["date"].max()
    df = name_products(filter_dates(df.copy(), start, end))
    if sku:
        df = df[df["sku"] == sku]
    if channel:
        df = df[df["channel"] == channel]
    if date:
        df = df[df["date"] == pd.Timestamp(date)]
    if city:
        df = df[df["city"] == city]
    if category:
        df = df[df["sku"].map(category_of) == category]
    if status == "stuck":
        df = df[(df["status"] == "shipping") & (df["date"] <= data_end - pd.Timedelta(days=STUCK_DAYS))]
    elif status in ORDER_FILTERS:
        df = df[df["status"].isin(ORDER_FILTERS[status])]
    df = df.sort_values(["date", "order_id"], ascending=[False, True])
    return {
        "count": int(df["order_id"].nunique()),
        "lines": int(len(df)),
        "total": int(df["total"].sum()),
        "orders": [
            {"order_id": r.order_id, "date": r.date.strftime("%Y-%m-%d"), "channel": r.channel, "sku": r.sku,
             "product": r.product, "category": category_of(r.sku), "qty": int(r.qty), "total": int(r.total),
             "status": r.status, "city": r.city or ""}
            for r in df.itertuples()
        ],
    }


def product_table(df: pd.DataFrame, start: str | None = None, end: str | None = None,
                  channel: str | None = None, category: str | None = None) -> dict:
    """Net revenue per product for the product table's own filters (dates, channel, category)."""
    df = name_products(filter_dates(df.copy(), start, end))
    if channel:
        df = df[df["channel"] == channel]
    if category:
        df = df[df["sku"].map(category_of) == category]
    counted = df[df["status"].isin(COUNTS_AS_REVENUE)]
    g = (counted.groupby(["sku", "product"], as_index=False)
         .agg(revenue=("total", "sum"), units=("qty", "sum"), orders=("order_id", "nunique"))
         .sort_values(["revenue", "sku"], ascending=[False, True]))
    return {
        "net_revenue": int(counted["total"].sum()),
        "products": [
            {"sku": r.sku, "product": r.product, "category": category_of(r.sku),
             "revenue": int(r.revenue), "units": int(r.units), "orders": int(r.orders)}
            for r in g.itertuples()
        ],
    }
