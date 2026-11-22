"""Optional AI helper: suggests what an unrecognised column or label means.

Only names and labels are sent (for example the column header "Waktu Order" or the
status "Menunggu Pickup"), never order rows. The suggestion is shown to the user,
who confirms it; the numbers are always calculated by analysis.py.

Uses Google Gemini's free tier when GEMINI_API_KEY is set. Without a key, or if the
call fails, the app still works: the user simply picks the answers themselves.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

try:
    from .analysis import CHANNELS, FIELDS, STATUS_GROUP_LABELS
except ImportError:
    from analysis import CHANNELS, FIELDS, STATUS_GROUP_LABELS

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


RETRY_WAITS = (2, 6)  # seconds between tries when Gemini is busy (503) or the free quota per minute is hit (429)
_cache: dict[str, dict] = {}  # same questions in this server run -> same answer, no extra quota


class AIUnavailable(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _ask(prompt: str) -> dict:
    """Calls Gemini, retrying briefly when it is busy or rate-limited."""
    if prompt in _cache:
        return _cache[prompt]
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            answer = _call(prompt)
            _cache[prompt] = answer
            return answer
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503) and attempt < len(RETRY_WAITS):
                time.sleep(RETRY_WAITS[attempt])
                continue
            if exc.code == 429:
                raise AIUnavailable("Saran AI sedang tidak tersedia (batas pemakaian gratis per menit tercapai).") from exc
            if exc.code == 503:
                raise AIUnavailable("Saran AI sedang tidak tersedia (layanan AI sedang sibuk).") from exc
            raise AIUnavailable(f"Saran AI gagal dimuat (kode {exc.code}).") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AIUnavailable("Saran AI tidak bisa dihubungi (cek koneksi internet).") from exc
    raise AIUnavailable("Saran AI sedang tidak tersedia.")


def _call(prompt: str) -> dict:
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }).encode()
    req = urllib.request.Request(
        URL.format(model=MODEL), data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]},
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        data = json.load(res)
    return json.loads(data["candidates"][0]["content"]["parts"][0]["text"])


def suggest(questions: dict) -> dict:
    """Fills in 'suggestion' for each question. Any answer outside the allowed options is thrown away."""
    questions["ai_used"] = False
    questions["ai_error"] = None
    if not available():
        return questions

    task = {
        "columns": {q["field"]: FIELDS[q["field"]] for q in questions["columns"]},
        "file_headers": questions["headers"],
        "channels": [q["value"] for q in questions["channels"]],
        "allowed_channels": CHANNELS,
        "statuses": [q["value"] for q in questions["statuses"]],
        "allowed_status_groups": STATUS_GROUP_LABELS,
    }
    prompt = (
        "You map messy Indonesian marketplace order exports (Shopee, TikTok Shop, Tokopedia) to a fixed schema.\n"
        "1. For each key in `columns`, pick the one header from `file_headers` that holds that field, or null.\n"
        "2. For each value in `channels`, pick one of `allowed_channels`, or null if it is not one of them.\n"
        "3. For each value in `statuses`, pick one key of `allowed_status_groups`, or null if unsure. "
        "A status that is still waiting or unpaid is not 'completed'.\n"
        'Answer only JSON: {"columns": {field: header|null}, "channels": {value: channel|null}, "statuses": {value: group|null}}\n\n'
        + json.dumps(task, ensure_ascii=False)
    )
    try:
        answer = _ask(prompt)
    except AIUnavailable as exc:
        questions["ai_error"] = exc.message  # shown on screen; the user simply picks the answers
        return questions
    except Exception:
        questions["ai_error"] = "Saran AI gagal dimuat."
        return questions  # AI is a helper, never a requirement

    for q in questions["columns"]:
        v = (answer.get("columns") or {}).get(q["field"])
        q["suggestion"] = v if v in questions["headers"] else None
    for q in questions["channels"]:
        v = (answer.get("channels") or {}).get(q["value"])
        q["suggestion"] = v if v in CHANNELS else None
    for q in questions["statuses"]:
        v = (answer.get("statuses") or {}).get(q["value"])
        q["suggestion"] = v if v in STATUS_GROUP_LABELS else None
    questions["ai_used"] = True
    return questions
