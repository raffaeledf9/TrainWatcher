"""One run of the GitHub job: drain the Worker's command queue, do due work, save state.

Logs are public (ADR 0002): print counts and timings only, never routes, prices, ids or tokens.
Env: WORKER_URL, WORKER_SECRET, TELEGRAM_TOKEN, OWNER_CHAT_ID, STATE_DB (default state.db).
"""
import json
import os
import sqlite3
import sys
import time
import urllib.request

from trainwatcher import tracer


def worker(path, body=None):
    req = urllib.request.Request(os.environ["WORKER_URL"].rstrip("/") + path, data=json.dumps(body or {}).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["WORKER_SECRET"], "Content-Type": "application/json",
                                          "User-Agent": "trainwatcher-job"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def telegram(method, **params):
    req = urllib.request.Request(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/{method}",
                                 data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def open_state(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    return db


def handle(item):
    if item["kind"] == "try":
        telegram("sendMessage", chat_id=item["user_id"], text=tracer.report(), parse_mode="HTML")
        return True
    return False  # unknown kinds are acked and dropped until later rounds handle them


def main():
    t0 = time.time()
    db = open_state(os.environ.get("STATE_DB", "state.db"))
    runs = int((db.execute("SELECT v FROM meta WHERE k='runs'").fetchone() or ["0"])[0]) + 1
    db.execute("INSERT INTO meta (k, v) VALUES ('runs', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (str(runs),))
    db.commit()
    handled = dropped = 0
    for _ in range(10):  # drain: commands may keep arriving while we work
        items = worker("/job/take")
        if not items:
            break
        for it in items:
            handled, dropped = (handled + 1, dropped) if handle(it) else (handled, dropped + 1)
        worker("/job/ack", {"ids": [it["id"] for it in items]})
    worker("/job/done")
    print(f"run #{runs}: commands handled={handled} dropped={dropped} time={time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
