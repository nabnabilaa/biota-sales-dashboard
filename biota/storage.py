"""Keeps every upload, so each weekly export adds to the data instead of replacing it.

Two backends, same behaviour:
- SQLite file (default storage/biota.db, or DB_PATH) for running on a laptop;
- Postgres when DATABASE_URL is set (e.g. Supabase), for the live site on serverless hosting.

One row per order line, identified by:
- the marketplace's own line ID when the export has one, otherwise
- order number + SKU + position (1st, 2nd... line of that SKU in the order).

On each upload:
- a line seen for the first time is added;
- a line seen again with any change (status, date, quantity, price) is updated, so the
  newest export always wins and a partial cancellation does not leave the old line behind;
- a line seen again unchanged is skipped, so overlapping exports never double count;
- stored orders that fall inside the new file's dates and channels but are missing from it
  are reported, and only removed when someone confirms.

It also remembers the answers people gave for unknown columns, channels and statuses,
so the same question is never asked twice.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "storage" / "biota.db"
KEY_VERSION = 2  # 1 = order|sku|qty|total, 2 = order|sku|position (or order|#line-id)

TABLES = """
CREATE TABLE IF NOT EXISTS orders (
    line_key      TEXT PRIMARY KEY,
    order_id      TEXT NOT NULL,
    sku           TEXT NOT NULL,
    date          TEXT NOT NULL,      -- YYYY-MM-DD
    channel       TEXT NOT NULL,
    listing_name  TEXT NOT NULL,
    qty           INTEGER NOT NULL,
    total         BIGINT NOT NULL,
    status        TEXT NOT NULL,
    city          TEXT NOT NULL DEFAULT '',
    sku_was_blank INTEGER NOT NULL DEFAULT 0,
    upload_id     INTEGER NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
    id          {id_type},
    filename    TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    report      TEXT NOT NULL         -- JSON: cleaning report + added/updated/unchanged
);
CREATE TABLE IF NOT EXISTS mappings (
    kind  TEXT NOT NULL,              -- columns | channels | statuses
    raw   TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (kind, raw)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""
SCHEMA = TABLES.format(id_type="INTEGER PRIMARY KEY AUTOINCREMENT") + ";"  # SQLite
SCHEMA_PG = TABLES.format(id_type="BIGSERIAL PRIMARY KEY")

COLUMNS = ["order_id", "sku", "date", "channel", "listing_name", "qty", "total", "status", "city", "sku_was_blank"]


def db_path() -> Path:
    return Path(os.environ.get("DB_PATH") or DEFAULT_PATH)


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "")


class DB:
    """The few database calls this module needs, written once for SQLite and Postgres."""

    def __init__(self, con, pg: bool):
        self.con, self.pg = con, pg

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.pg else sql

    def execute(self, sql: str, params=()):
        cur = self.con.cursor()
        cur.execute(self._sql(sql), params)
        return cur

    def executemany(self, sql: str, rows):
        if rows:
            self.con.cursor().executemany(self._sql(sql), rows)

    def all(self, sql: str, params=()):
        return self.execute(sql, params).fetchall()

    def one(self, sql: str, params=()):
        return self.execute(sql, params).fetchone()

    def insert_id(self, sql: str, params=()) -> int:
        if self.pg:
            return self.execute(sql + " RETURNING id", params).fetchone()[0]
        return self.execute(sql, params).lastrowid

    def upsert_mapping_sql(self) -> str:
        if self.pg:
            return "INSERT INTO mappings (kind, raw, value) VALUES (?, ?, ?) ON CONFLICT (kind, raw) DO UPDATE SET value = EXCLUDED.value"
        return "INSERT OR REPLACE INTO mappings (kind, raw, value) VALUES (?, ?, ?)"

    def set_meta(self, key: str, value: str) -> None:
        if self.pg:
            self.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, value))
        else:
            self.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


_pg_ready = False  # tables created once per warm serverless instance


@contextmanager
def connect():
    global _pg_ready
    url = database_url()
    if url:
        import psycopg  # only needed for the live site
        # prepare_threshold=None: works through Supabase's transaction pooler.
        con = psycopg.connect(url, prepare_threshold=None, connect_timeout=10)
        db = DB(con, pg=True)
        try:
            if not _pg_ready:
                for stmt in SCHEMA_PG.split(";"):
                    if stmt.strip():
                        db.execute(stmt)
                db.set_meta("key_version", str(KEY_VERSION))
                con.commit()
                _pg_ready = True
            yield db
            con.commit()
        finally:
            con.close()
        return
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        db = DB(con, pg=False)
        _migrate(db)
        yield db
        con.commit()
    finally:
        con.close()


def _migrate(db: DB) -> None:
    """SQLite only: moves line keys written by the first version (order|sku|qty|total) to order|sku|position."""
    row = db.one("SELECT value FROM meta WHERE key = 'key_version'")
    version = int(row[0]) if row else (1 if db.one("SELECT 1 FROM orders LIMIT 1") else KEY_VERSION)
    if version < 2:
        rows = db.all("SELECT rowid, order_id, sku FROM orders ORDER BY rowid")
        seen: dict[tuple, int] = {}
        db.execute("UPDATE orders SET line_key = 'tmp:' || rowid")
        for rowid, order_id, sku in rows:
            n = seen[(order_id, sku)] = seen.get((order_id, sku), 0) + 1
            db.execute("UPDATE orders SET line_key = ? WHERE rowid = ?", (f"{order_id}|{sku}|{n}", rowid))
    db.set_meta("key_version", str(KEY_VERSION))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def line_keys(df: pd.DataFrame) -> list[str]:
    """Key per line: the line ID when the export has one, else order + SKU + position."""
    position = df.groupby(["order_id", "sku"]).cumcount() + 1
    line_id = df["line_id"] if "line_id" in df else pd.Series("", index=df.index)
    return [
        f"{o}|#{lid}" if lid else f"{o}|{s}|{n}"
        for o, s, n, lid in zip(df["order_id"], df["sku"], position, line_id)
    ]


# ---------- mappings ----------

def load_mappings() -> dict:
    with connect() as db:
        out: dict = {}
        for kind, raw, value in db.all("SELECT kind, raw, value FROM mappings"):
            out.setdefault(kind, {})[raw] = value
        return out


def save_mappings(mapping: dict | None) -> None:
    rows = [(kind, raw, value) for kind, pairs in (mapping or {}).items() for raw, value in pairs.items() if value]
    if not rows:
        return
    with connect() as db:
        db.executemany(db.upsert_mapping_sql(), rows)


def merge_mappings(saved: dict, given: dict | None) -> dict:
    out = {k: dict(v) for k, v in saved.items()}
    for kind, pairs in (given or {}).items():
        out.setdefault(kind, {}).update(pairs)
    return out


# ---------- orders ----------

def known_names() -> dict:
    """Listing name (lowercased) -> SKU, learned from everything stored so far."""
    with connect() as db:
        rows = db.all("SELECT DISTINCT listing_name, sku FROM orders WHERE sku_was_blank = 0")
    return {" ".join(name.lower().split()): sku for name, sku in rows}


def save_upload(df: pd.DataFrame, filename: str, report: dict) -> dict:
    """Adds or updates the cleaned rows of one file. Returns how many were new / updated / unchanged."""
    now = _now()
    keys = line_keys(df)
    with connect() as db:
        upload_id = db.insert_id("INSERT INTO uploads (filename, uploaded_at, report) VALUES (?, ?, '{}')", (filename, now))
        existing = {
            key: (date, status, int(qty), int(total))
            for key, date, status, qty, total in db.all("SELECT line_key, date, status, qty, total FROM orders")
        }
        inserts, updates, unchanged = [], [], 0
        for key, r in zip(keys, df.itertuples(index=False)):
            date = r.date.strftime("%Y-%m-%d")
            values = (r.order_id, r.sku, date, r.channel, r.listing_name, int(r.qty), int(r.total),
                      r.status, r.city or "", int(bool(r.sku_was_blank)), upload_id, now)
            if key not in existing:
                inserts.append((key, *values))
                existing[key] = (date, r.status, int(r.qty), int(r.total))
            elif existing[key] != (date, r.status, int(r.qty), int(r.total)):
                updates.append((*values, key))
            else:
                unchanged += 1
        # Batched so an upload of hundreds of lines is a few round trips, not hundreds.
        db.executemany(
            "INSERT INTO orders (line_key, order_id, sku, date, channel, listing_name, qty, total, status, city, sku_was_blank, upload_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", inserts)
        db.executemany(
            "UPDATE orders SET order_id=?, sku=?, date=?, channel=?, listing_name=?, qty=?, total=?, status=?, city=?, sku_was_blank=?, upload_id=?, updated_at=? "
            "WHERE line_key=?", updates)
        result = {**report, "added": len(inserts), "updated": len(updates), "unchanged": unchanged}
        db.execute("UPDATE uploads SET report=? WHERE id=?", (json.dumps(result, ensure_ascii=False), upload_id))
    return result


def missing_orders(df: pd.DataFrame, order_ids_in_file: list[str]) -> list[dict]:
    """Stored orders inside the new file's dates and channels that the file does not contain.

    Only reported, never removed here: an export filtered by status would otherwise wipe data.
    """
    if df.empty:
        return []
    start, end = df["date"].min().strftime("%Y-%m-%d"), df["date"].max().strftime("%Y-%m-%d")
    channels = sorted(df["channel"].unique())
    in_file = set(order_ids_in_file)
    marks = ",".join("?" * len(channels))
    with connect() as db:
        agg = "STRING_AGG(DISTINCT status, ',')" if db.pg else "GROUP_CONCAT(DISTINCT status)"
        rows = db.all(
            f"SELECT order_id, MIN(date), channel, SUM(total), {agg}, COUNT(*) FROM orders "
            f"WHERE date BETWEEN ? AND ? AND channel IN ({marks}) GROUP BY order_id, channel ORDER BY MIN(date), order_id",
            (start, end, *channels))
    return [
        {"order_id": o, "date": d, "channel": c, "total": int(t), "status": st, "lines": int(n)}
        for o, d, c, t, st, n in rows if o not in in_file
    ]


def remove_orders(order_ids: list[str]) -> int:
    """Removes whole orders (all their lines). Called only after the user confirms."""
    if not order_ids:
        return 0
    with connect() as db:
        marks = ",".join("?" * len(order_ids))
        removed = db.execute(f"DELETE FROM orders WHERE order_id IN ({marks})", order_ids).rowcount
        report = {"removed_orders": len(order_ids), "removed_lines": removed, "added": 0, "updated": 0, "unchanged": 0}
        db.execute("INSERT INTO uploads (filename, uploaded_at, report) VALUES (?, ?, ?)",
                   (f"Hapus {len(order_ids)} pesanan yang tidak ada di ekspor terbaru", _now(), json.dumps(report)))
    return removed


def load_orders() -> pd.DataFrame:
    with connect() as db:
        rows = db.all(f"SELECT {', '.join(COLUMNS)} FROM orders")
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["date"] = pd.to_datetime(df["date"])
    df["qty"] = df["qty"].astype(int)
    df["total"] = df["total"].astype("int64")
    df["sku_was_blank"] = df["sku_was_blank"].astype(bool)
    return df


def uploads(limit: int = 10) -> list[dict]:
    with connect() as db:
        rows = db.all("SELECT id, filename, uploaded_at, report FROM uploads ORDER BY id DESC LIMIT ?", (limit,))
    return [{"id": i, "filename": f, "uploaded_at": t, **json.loads(r)} for i, f, t, r in rows]


def clear() -> None:
    with connect() as db:
        for table in ("orders", "uploads", "mappings"):
            db.execute(f"DELETE FROM {table}")
