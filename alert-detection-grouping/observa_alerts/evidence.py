"""Bounded local evidence tool. No network requests; requires a pinned snapshot."""
import argparse
import hashlib
import json
from pathlib import Path
from .report import SNAPSHOT_ID, encoded, incident_root


def checked_file(path, maximum=16 * 1024 * 1024):
    if path.stat().st_size > maximum:
        raise ValueError("File exceeds reader size limit")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("File exceeds reader size limit")
    return raw


def load_manifest(reports, incident_id, snapshot_id):
    if not SNAPSHOT_ID.fullmatch(snapshot_id):
        raise ValueError("Invalid snapshot ID")
    root = incident_root(reports, incident_id)
    snapshot = (root / "snapshots" / snapshot_id).resolve()
    input_path, manifest_path = (snapshot / "incident.json").resolve(), (snapshot / "manifest.json").resolve()
    if not input_path.is_relative_to(root) or not manifest_path.is_relative_to(root):
        raise ValueError("Snapshot escapes incident directory")
    saved = json.loads(checked_file(input_path, 1024 * 1024))
    raw = checked_file(manifest_path, 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != saved.get("manifest_sha256"):
        raise ValueError("Manifest checksum mismatch")
    manifest = json.loads(raw)
    for document in (saved, manifest):
        if document.get("snapshot_id") != snapshot_id or document.get("id", document.get("incident_id")) != incident_id:
            raise ValueError("Snapshot identity mismatch")
    return root, manifest


def read_evidence(reports, incident_id, snapshot_id, ref_id, cursor=0, limit=20, max_bytes=16384):
    """Return records, next_cursor, provenance, and metadata within a UTF-8 JSON byte budget.

    Start with ref_id='traces', then request a returned trace ref to page through spans.
    On record_too_large increase max_bytes (maximum 65536); never silently skip a record.
    """
    if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Use cursor >= 0 and limit between 1 and 100")
    if type(max_bytes) is not int or not 1024 <= max_bytes <= 65536:
        raise ValueError("max_bytes must be between 1024 and 65536")
    root, manifest = load_manifest(reports, incident_id, snapshot_id)
    if ref_id not in manifest["refs"]:
        raise ValueError("Unknown evidence ref")
    ref = manifest["refs"][ref_id]
    relative = Path(ref["path"])
    if relative.is_absolute() or not relative.parts or relative.parts[0] != "evidence" or ".." in relative.parts:
        raise ValueError("Invalid evidence path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.suffix != ".jsonl":
        raise ValueError("Evidence path escapes incident directory")
    raw = checked_file(path)
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise ValueError("Evidence checksum mismatch")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]
    if len(rows) != ref["record_count"] or cursor > len(rows):
        raise ValueError("Invalid record count or cursor")
    result = {"incident_id": incident_id, "snapshot_id": snapshot_id, "ref_id": ref_id,
              "kind": ref["kind"], "origin": manifest["origin"], "saved_evidence": True,
              "query_window": manifest["query_window"], "metadata": ref["metadata"],
              "total_records": len(rows), "cursor": cursor, "items": [], "next_cursor": None,
              "has_more": False, "error": None}
    if len(encoded(result).encode()) > max_bytes - 256:
        raise ValueError("Metadata exceeds budget; increase max_bytes")
    for position in range(cursor, min(len(rows), cursor + limit)):
        result["items"].append(rows[position])
        result["next_cursor"] = position + 1 if position + 1 < len(rows) else None
        result["has_more"] = result["next_cursor"] is not None
        if len(encoded(result).encode()) > max_bytes:
            result["items"].pop()
            result["next_cursor"] = position
            result["has_more"] = True
            if not result["items"]:
                result["error"] = {"code": "record_too_large", "cursor": position,
                                   "message": "Increase max_bytes up to 65536; record was not discarded."}
            break
    assert len(encoded(result).encode()) <= max_bytes
    return result


def main():
    parser = argparse.ArgumentParser(description="Read a pinned incident evidence ref")
    parser.add_argument("--reports", default="reports")
    parser.add_argument("--incident", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--cursor", type=int, default=0)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-bytes", type=int, default=16384)
    args = parser.parse_args()
    try:
        result = read_evidence(args.reports, args.incident, args.snapshot, args.ref,
                               args.cursor, args.limit, args.max_bytes)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"Evidence unavailable: {exc}\n")
    print(encoded(result))


if __name__ == "__main__":
    main()
