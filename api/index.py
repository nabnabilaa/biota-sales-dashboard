"""Web API.

  POST /api/login       team password -> session cookie (only when APP_PASSWORD is set)
  GET  /api/session     is a password needed, and is this browser logged in
  POST /api/upload      add one export file to the stored data
  GET  /api/dashboard   numbers for a date range, from everything stored
  GET  /api/orders      order lines behind a number (product / channel / day / status / city / category)
  GET  /api/products    product table with its own filters
  POST /api/remove-orders  remove orders the user confirmed (missing from a newer export)
  DELETE /api/data      remove all stored data

Locally this also serves the page, so one command runs everything:
    uvicorn api.index:app --reload
"""
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root, also on Vercel
from biota import ai, storage  # noqa: E402
from biota.analysis import (  # noqa: E402
    CHANNELS, FIELDS, STATUS_GROUP_LABELS, DataError, NeedsMapping, build, ingest, order_lines, product_table,
)

MAX_BYTES = 4 * 1024 * 1024  # Vercel rejects request bodies above 4.5 MB
COOKIE = "biota_session"

app = FastAPI(title="BIOTA Sales Dashboard")

# Lets the page work when it is opened from another local web server (e.g. Laragon on port 80).
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
    allow_credentials=True,
)


# ---------- team password ----------

def _password() -> str:
    return os.environ.get("APP_PASSWORD", "")


def _token() -> str:
    secret = (os.environ.get("SESSION_SECRET") or _password()).encode()
    return hmac.new(secret, b"biota-dashboard", hashlib.sha256).hexdigest()


def require_login(request: Request) -> None:
    if not _password():
        return  # no password set (local use): open
    if not hmac.compare_digest(request.cookies.get(COOKIE, ""), _token()):
        raise HTTPException(401, "Masukkan kata sandi tim.")


@app.get("/api/session")
def session(request: Request):
    needed = bool(_password())
    ok = not needed or hmac.compare_digest(request.cookies.get(COOKIE, ""), _token())
    return {"password_required": needed, "logged_in": ok}


@app.post("/api/login")
async def login(response: Response, request: Request, password: str = Form(...)):
    if not _password():
        return {"logged_in": True}
    if not hmac.compare_digest(password.encode(), _password().encode()):
        await asyncio.sleep(1)  # slows down guessing
        raise HTTPException(401, "Kata sandi salah.")
    response.set_cookie(COOKIE, _token(), httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", max_age=60 * 60 * 24 * 30)
    return {"logged_in": True}


# ---------- data ----------

def dashboard_payload(start: str | None, end: str | None) -> dict:
    df = storage.load_orders()
    history = storage.uploads()
    if df.empty:
        return {"empty": True, "uploads": history}
    if start and end and start > end:
        start, end = end, start
    data = build(df, start or None, end or None, history[0] if history else {})
    data["uploads"] = history
    data["stored_rows"] = int(len(df))
    return data


@app.get("/api/dashboard", dependencies=[Depends(require_login)])
def dashboard(start: str | None = None, end: str | None = None):
    return dashboard_payload(start, end)


@app.get("/api/orders", dependencies=[Depends(require_login)])
def orders(start: str | None = None, end: str | None = None, sku: str | None = None,
           channel: str | None = None, date: str | None = None, status: str | None = None,
           city: str | None = None, category: str | None = None):
    """Order lines behind a number: filter by product, channel, day and/or status."""
    df = storage.load_orders()
    if df.empty:
        return {"count": 0, "lines": 0, "total": 0, "orders": []}
    return order_lines(df, start, end, sku, channel, date, status, city, category)


@app.get("/api/products", dependencies=[Depends(require_login)])
def products(start: str | None = None, end: str | None = None, channel: str | None = None, category: str | None = None):
    """Product table with its own date range, channel and category filters."""
    df = storage.load_orders()
    if df.empty:
        return {"net_revenue": 0, "products": []}
    return product_table(df, start, end, channel, category)


@app.post("/api/upload", dependencies=[Depends(require_login)])
async def upload(file: UploadFile = File(...), mapping: str | None = Form(None)):
    content = await file.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "File terlalu besar (maksimal 4 MB).")
    try:
        given = json.loads(mapping) if mapping else None
    except json.JSONDecodeError:
        given = None
    # Answers given on earlier uploads are reused, so nobody is asked the same thing twice.
    answers = storage.merge_mappings(storage.load_mappings(), given)
    name = file.filename or "data.csv"
    try:
        df, report = ingest(content, name, answers, storage.known_names())
    except NeedsMapping as need:
        # 409: the file is fine, but someone must confirm what some columns or labels mean.
        questions = ai.suggest(need.questions)
        questions["options"] = {"fields": FIELDS, "channels": CHANNELS, "statuses": STATUS_GROUP_LABELS}
        return JSONResponse(status_code=409, content={"detail": str(need), "questions": questions})
    except DataError as exc:
        raise HTTPException(422, str(exc)) from exc
    storage.save_mappings(given)
    order_ids = report.pop("_order_ids", [])
    result = storage.save_upload(df, name, report)
    # Older stored orders in this file's dates/channels that the file no longer has: shown, not removed.
    missing = storage.missing_orders(df, order_ids)
    return {"upload": {"filename": name, **result}, "missing": missing, **dashboard_payload(None, None)}


@app.post("/api/remove-orders", dependencies=[Depends(require_login)])
async def remove_orders(request: Request):
    """Removes whole orders after the user confirmed it (e.g. orders missing from a newer export)."""
    body = await request.json()
    ids = [str(x) for x in body.get("order_ids", []) if str(x).strip()]
    removed = storage.remove_orders(ids)
    return {"removed_lines": removed, **dashboard_payload(None, None)}


@app.delete("/api/data", dependencies=[Depends(require_login)])
def clear_data():
    storage.clear()
    return {"empty": True, "uploads": []}


# Serve only the page and its assets, never the whole folder (data/ and storage/ stay private).
ROOT = Path(__file__).resolve().parent.parent
if (ROOT / "index.html").exists():
    app.mount("/src", StaticFiles(directory=ROOT / "src"), name="assets")

    @app.get("/", include_in_schema=False)
    def page():
        return FileResponse(ROOT / "index.html")
