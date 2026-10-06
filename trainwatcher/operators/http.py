"""Tiny HTTP layer shared by the connectors: JSON calls, outcome classification, polite parallelism."""
import gzip
import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPException

from ..offers import BLOCKED, BROKEN, OK, SearchResult

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
_OPENER = urllib.request.build_opener()


def request(method, url, body=None, headers=None, opener=None, timeout=30):
    """Send one JSON request. -> (HTTP status, parsed JSON or text).

    A network failure (timeout, reset, DNS) is retried once; if it persists the status is None and the text
    is the error's type name. HTTP error statuses are returned, never retried."""
    h = {"User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "gzip", **(headers or {})}
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    for attempt in (0, 1):
        try:
            status, raw = _send(req, opener or _OPENER, timeout)
            break
        except (OSError, HTTPException) as e:
            if attempt:
                return None, type(e).__name__
            time.sleep(1)
    text = raw.decode("utf-8", "replace")
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def _send(req, opener, timeout):
    try:
        with opener.open(req, timeout=timeout) as r:
            status, raw, enc = r.status, r.read(), r.headers.get("Content-Encoding")
    except urllib.error.HTTPError as e:
        with e:
            status, raw, enc = e.code, e.read(), e.headers.get("Content-Encoding")
    return status, gzip.decompress(raw) if enc == "gzip" else raw


def classify(status, body):
    """Map one HTTP outcome to (OK | BLOCKED | BROKEN, log-safe detail). OK means "well-formed JSON, parse it".

    Connectors handle their own "valid but empty" answers (EMPTY) before calling this."""
    if status is None:
        return BROKEN, f"network: {body}"
    if status in (403, 429):
        return BLOCKED, f"http {status}"
    if status >= 500:
        return BROKEN, f"http {status}"
    if isinstance(body, str) and "<html" in body[:2000].lower():
        return BLOCKED, f"challenge page (http {status})"
    if status >= 300:
        return BROKEN, f"http {status}"
    if not isinstance(body, (dict, list)):
        return BROKEN, "not json"
    return OK, ""


def run_parallel(calls, max_workers):
    """Run zero-argument callables that return SearchResults, at most max_workers at a time.

    Every BLOCKED result halves the limit (never below 1) for the calls still waiting.
    -> (results in input order, final limit: carry it into the rest of the run)."""
    limit, active = max(1, max_workers), 0
    cond = threading.Condition()

    def one(call):
        nonlocal limit, active
        with cond:
            cond.wait_for(lambda: active < limit)
            active += 1
        try:
            res = call()
        except Exception as e:  # connectors don't raise; never let one bug sink the whole run
            res = SearchResult(BROKEN, detail=f"crash: {type(e).__name__}")
        with cond:
            active -= 1
            if res.status == BLOCKED:
                limit = max(1, limit // 2)
            cond.notify_all()
        return res

    with ThreadPoolExecutor(max(1, max_workers)) as ex:
        results = list(ex.map(one, calls))
    return results, limit
