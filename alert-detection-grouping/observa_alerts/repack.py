"""Convert flat v1 reports offline. Stop the detector before use; archive, never delete."""
import argparse
import hashlib
import json
from pathlib import Path
import uuid
from .report import INCIDENT_ID, export, write_atomic, write_index
from .evidence import load_manifest


def repack(directory):
    root = Path(directory).resolve()
    sources = sorted(root.glob("INC-*.json"))
    if not sources:
        return {"converted": 0, "archive": None}
    originals = []
    for source in sources:
        if source.resolve().parent != root:
            raise ValueError("Legacy report escapes reports directory")
        incident = json.loads(source.read_text(encoding="utf-8"))
        if not INCIDENT_ID.fullmatch(source.stem) or incident["id"] != source.stem or incident["schema_version"] != "1.0":
            raise ValueError("Unexpected legacy report identity or format")
        originals.append(incident)
    existing = {}
    for folder in root.glob("INC-*"):
        if folder.is_dir() and (folder / "incident.json").is_file():
            saved = json.loads((folder / "incident.json").read_text(encoding="utf-8"))
            existing[saved["id"]] = saved
    archive = (root / "_legacy" / ("v1-" + uuid.uuid4().hex[:12])).resolve()
    if not archive.is_relative_to(root):
        raise ValueError("Archive escapes reports directory")
    archive.mkdir(parents=True)
    for name in ("index.json", "index.md"):
        source = root / name
        if source.exists():
            write_atomic(archive / name, source.read_text(encoding="utf-8"))
    to_export = [i for i in originals if i["id"] not in existing or existing[i["id"]]["revision"] <= i["revision"]]
    exported = export(to_export, root)
    existing.update({i["id"]: i for i in exported})
    # All new references must be readable before moving any original report.
    for incident in exported:
        bundle_root, manifest = load_manifest(root, incident["id"], incident["snapshot_id"])
        for ref in manifest["refs"].values():
            target = (bundle_root / ref["path"]).resolve()
            if not target.is_relative_to(bundle_root) or hashlib.sha256(target.read_bytes()).hexdigest() != ref["sha256"]:
                raise ValueError("Failed evidence validation; originals retained")
    write_index(list(existing.values()), root)
    for source in sources:
        for old in (source, source.with_suffix(".md")):
            if old.exists():
                target = (archive / old.name).resolve()
                if old.resolve().parent != root or not target.is_relative_to(root) or target.exists():
                    raise ValueError("Unsafe archive destination")
                old.rename(target)
    return {"converted": len(exported), "archived": len(originals), "archive": str(archive)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", default="reports")
    args = parser.parse_args()
    print(json.dumps(repack(args.reports), indent=2))


if __name__ == "__main__":
    main()
