"""Durable jobs pinned to F1 snapshots. One opening and one job per resolved revision."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

MAX_ATTEMPTS = 8
CLAIM_LEASE_SECONDS = 900


def discover(db, reports: Path):
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS report_jobs (
            incident TEXT NOT NULL, phase TEXT NOT NULL, snapshot TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
            retry_at REAL NOT NULL DEFAULT 0, claimed_until REAL NOT NULL DEFAULT 0,
            last_error TEXT, PRIMARY KEY (incident, phase, snapshot))""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(report_jobs)")}
        if "claimed_until" not in columns:
            db.execute("ALTER TABLE report_jobs ADD COLUMN claimed_until REAL NOT NULL DEFAULT 0")
        if "last_error" not in columns:
            db.execute("ALTER TABLE report_jobs ADD COLUMN last_error TEXT")
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


def unanswered_legacy_request(result):
    # Before F2 follow-ups were dispatched, F3 saved the request and omitted
    # the report's follow_up_answers field entirely.
    return (result.get("agent_status") == "needs_more_analysis"
            and bool(result.get("follow_up_request"))
            and "follow_up_answers" not in result["report"])


def resume_legacy_requests(db, reports: Path, output: Path):
    with db:
        db.execute("BEGIN IMMEDIATE")
        paths = list(output.glob("INC-*/correlation*.json"))
        paths.extend(output.glob("INC-*/f3/.state/correlation*.json"))
        for path in paths:
            if path.parent.parent == output and (
                    output / path.parent.name / "f3" / ".state" / path.name).exists():
                continue
            if path.name not in ("correlation.initial.json", "correlation.json"):
                continue
            try:
                data = json.loads(path.read_bytes())
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            if not unanswered_legacy_request(data):
                continue
            history = data["report"].get("history") or {}
            iid = path.parent.name
            phase, sid = history.get("phase"), history.get("snapshot_id")
            if (data.get("incident_id") != iid or phase not in ("initial", "final")
                    or not sid or not (reports / iid / "snapshots" / sid / "incident.json").is_file()):
                continue
            # The unsuffixed file can be a mirror of the opening report.
            if phase == "initial" and path.name != "correlation.initial.json":
                continue
            db.execute("""UPDATE report_jobs SET done=0, attempts=0, retry_at=0
                          WHERE incident=? AND phase=? AND snapshot=? AND done=1""", (iid, phase, sid))


def process_next(db, reports: Path, output: Path, process, on_failure=None) -> bool:
    now = time.time()
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""SELECT rowid, incident, snapshot, attempts, phase FROM report_jobs
                            WHERE done=0 AND retry_at <= ? AND claimed_until <= ?
                            ORDER BY rowid LIMIT 1""", (now, now)).fetchone()
        if row is None:
            return False
        key, iid, snapshot, attempts, phase = row
        db.execute("UPDATE report_jobs SET claimed_until=? WHERE rowid=?",
                   (now + CLAIM_LEASE_SECONDS, key))

    print(f"[{iid}] Starting {phase} report, attempt {attempts + 1}/{MAX_ATTEMPTS}, snapshot {snapshot}.")
    try:
        process(reports, iid, output / iid, snapshot)
    except Exception as exc:
        next_attempt = attempts + 1
        retryable = getattr(exc, "retryable", True)
        failed = not retryable or next_attempt >= MAX_ATTEMPTS
        delay = min(300, 5 * 2 ** min(attempts, 6))
        message = f"{type(exc).__name__}: {exc}"[:1000]
        with db:
            if failed:
                db.execute("""UPDATE report_jobs SET attempts=?, done=1, claimed_until=0,
                              last_error=? WHERE rowid=?""", (next_attempt, message, key))
                if retryable:
                    reason = f"after {next_attempt}/{MAX_ATTEMPTS} attempts"
                else:
                    reason = f"without retry because the error is non-retryable"
                print(f"[{iid}] {phase} report failed {reason}. Last error: {message}. Job is marked failed.")
            else:
                db.execute("""UPDATE report_jobs SET attempts=?, retry_at=?, claimed_until=0,
                              last_error=? WHERE rowid=?""",
                           (next_attempt, time.time() + delay, message, key))
                print(f"[{iid}] {phase} report is still pending after attempt {next_attempt}/{MAX_ATTEMPTS}. "
                      f"Last error: {message}. Next retry in {delay}s.")
        if failed and on_failure is not None:
            try:
                on_failure(reports, iid, output / iid, snapshot, phase, message, retryable, type(exc).__name__)
            except Exception as publish_error:
                print(f"[{iid}] Could not publish failed F3 result: {type(publish_error).__name__}: {publish_error}")
    else:
        with db:
            db.execute("""UPDATE report_jobs SET done=1, claimed_until=0, last_error=NULL
                          WHERE rowid=?""", (key,))
        print(f"[{iid}] {phase} report job is complete for snapshot {snapshot}.")
    return True


def watch(reports: Path, output: Path, process):
    if not reports.is_dir():
        raise ValueError(f"Missing F1 reports directory: {reports}")
    output.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(output / ".watch.sqlite3", timeout=300)) as db:
        print(f"Monitoring {reports} for open and resolved incidents.")
        discover(db, reports)
        resume_legacy_requests(db, reports, output)
        last_waiting = None
        while True:
            if not process_next(db, reports, output, process, getattr(process, "on_failure", None)):
                waiting = db.execute("""SELECT incident, phase, snapshot, attempts, retry_at
                                        FROM report_jobs WHERE done=0 ORDER BY rowid""").fetchall()
                signature = tuple((iid, phase, snapshot, attempts, retry_at > time.time())
                                  for iid, phase, snapshot, attempts, retry_at in waiting)
                if signature != last_waiting:
                    if waiting:
                        print(f"Waiting for {len(waiting)} report job(s). They remain tracked while F1 "
                              "incidents are open or retries are scheduled.")
                        for iid, phase, snapshot, attempts, retry_at in waiting:
                            if retry_at > time.time():
                                print(f"[{iid}] {phase} report pending for {snapshot}; retry scheduled "
                                      f"in {max(1, int(retry_at-time.time()))}s "
                                      f"(attempts {attempts}/{MAX_ATTEMPTS}).")
                    else:
                        print("No queued report jobs. Monitoring F1 for new or updated incidents.")
                    last_waiting = signature
                time.sleep(2)
            else:
                last_waiting = None
            discover(db, reports)
