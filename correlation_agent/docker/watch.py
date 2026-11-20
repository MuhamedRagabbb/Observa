"""Durable jobs pinned to F1 snapshots. One opening and one job per resolved revision."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time


def discover(db, reports: Path):
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS report_jobs (
            incident TEXT NOT NULL, phase TEXT NOT NULL, snapshot TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
            retry_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (incident, phase, snapshot))""")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_initial ON report_jobs(incident)
                      WHERE phase='initial'""")
        # Respect the old watcher's explicit baseline on upgrade. Newly discovered
        # IDs, including incidents closed during downtime, still get a final report.
        legacy = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='incidents'").fetchone()
        excluded = {r[0] for r in db.execute("SELECT id FROM incidents WHERE status='existing'")} if legacy else set()
        for path in sorted(reports.glob("INC-*/incident.json")):
            if path.parent.name in excluded:
                continue
            try:
                doc = json.loads(path.read_bytes())
                sid = doc.get("snapshot_id")
                if not sid or path.parent.name != doc.get("id") or doc.get("status") not in ("open", "resolved"):
                    continue
                phase = "final" if doc["status"] == "resolved" else "initial"
                if phase == "final":
                    db.execute("""UPDATE report_jobs SET done=1 WHERE incident=? AND phase=\'final\'
                                  AND done=0 AND snapshot!=?""", (doc["id"], sid))
                db.execute("INSERT OR IGNORE INTO report_jobs(incident, phase, snapshot) VALUES (?, ?, ?)",
                           (doc["id"], phase, sid))
            except (FileNotFoundError, json.JSONDecodeError):
                continue  # Another publication can be discovered on the next scan.


def process_next(db, reports: Path, output: Path, process) -> bool:
    with db:
        # Hold the claim until publication. A crash rolls back the claim; output
        # publication is idempotent even if SQLite did not record completion.
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""SELECT rowid, incident, snapshot, attempts, phase FROM report_jobs
                            WHERE done=0 AND retry_at <= ? ORDER BY rowid LIMIT 1""",
                         (time.time(),)).fetchone()
        if row is None:
            return False
        key, iid, snapshot, attempts, phase = row
        try:
            process(reports, iid, output / iid, snapshot)
        except Exception as exc:
            delay = min(300, 5 * 2 ** min(attempts, 6))
            db.execute("UPDATE report_jobs SET attempts=attempts+1, retry_at=? WHERE rowid=?",
                       (time.time() + delay, key))
            print(f"{iid}: {phase} report pending at {snapshot}: {type(exc).__name__}: {exc}. Retrying in {delay}s.")
        else:
            db.execute("UPDATE report_jobs SET done=1 WHERE rowid=?", (key,))
        return True


def watch(reports: Path, output: Path, process):
    if not reports.is_dir():
        raise ValueError(f"Missing F1 reports directory: {reports}")
    output.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(output / ".watch.sqlite3", timeout=300)) as db:
        print(f"Monitoring {reports} for open and resolved incidents.")
        while True:
            discover(db, reports)
            if not process_next(db, reports, output, process):
                time.sleep(2)
