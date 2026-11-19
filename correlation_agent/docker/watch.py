"""Persistent queue of new incident IDs. F1 publishes incident.json atomically."""
from contextlib import closing
from pathlib import Path
import sqlite3
import time


def discover(db, reports: Path):
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, attempts INTEGER DEFAULT 0,
            retry_at REAL DEFAULT 0)""")
        first_scan = db.execute("PRAGMA user_version").fetchone()[0] == 0
        # Only the first scan establishes a baseline. A restart catches up on
        # incidents exported while the worker was offline.
        ids = sorted(path.parent.name for path in reports.glob("INC-*/incident.json"))
        db.executemany("INSERT OR IGNORE INTO incidents(id, status) VALUES (?, ?)",
                       ((iid, "existing" if first_scan else "pending") for iid in ids))
        if first_scan:
            db.execute("PRAGMA user_version=1")
            print(f"Watching new F1 incidents; skipped {len(ids)} already present at startup.")


def process_next(db, reports: Path, output: Path, process) -> bool:
    with db:
        # Serialize claims even if two containers share the queue. On shutdown
        # an unfinished claim rolls back; published stage files can be reused.
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""SELECT id, attempts FROM incidents
                            WHERE status='pending' AND retry_at <= ?
                            ORDER BY rowid LIMIT 1""", (time.time(),)).fetchone()
        if row is None:
            return False
        iid, attempts = row
        try:
            process(reports, iid, output / iid)
        except Exception as exc:
            delay = min(300, 5 * 2 ** min(attempts, 6))
            db.execute("UPDATE incidents SET attempts=attempts+1, retry_at=? WHERE id=?",
                       (time.time() + delay, iid))
            print(f"{iid}: {type(exc).__name__}: {exc}. Retrying in {delay}s.")
        else:
            db.execute("UPDATE incidents SET status='done' WHERE id=?", (iid,))
        return True


def watch(reports: Path, output: Path, process):
    if not reports.is_dir():
        raise ValueError(f"Missing F1 reports directory: {reports}")
    output.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(output / ".watch.sqlite3", timeout=300)) as db:
        print(f"Monitoring {reports} for new incidents.")
        while True:
            discover(db, reports)
            if not process_next(db, reports, output, process):
                time.sleep(2)
