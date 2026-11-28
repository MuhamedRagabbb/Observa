"""Evidence resolution for F4 validation without exposing ground truth."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Protocol

from .contracts import EvidenceRef, IncidentWindow, SignalType, parse_utc


class EvidenceResolver(Protocol):
    """Read-only resolver returning EvidenceRef metadata or None when unresolvable."""

    def resolve(self, evidence_id: str) -> EvidenceRef | None: ...


class CatalogEvidenceResolver:
    """In-memory EvidenceRef catalog for unit/contract tests and standalone runs."""

    def __init__(self, refs: list[EvidenceRef] | dict[str, EvidenceRef] | None = None):
        if refs is None:
            self._refs: dict[str, EvidenceRef] = {}
        elif isinstance(refs, dict):
            self._refs = dict(refs)
        else:
            self._refs = {ref.evidence_id: ref for ref in refs}

    def add(self, ref: EvidenceRef) -> None:
        self._refs[ref.evidence_id] = ref

    def resolve(self, evidence_id: str) -> EvidenceRef | None:
        return self._refs.get(evidence_id)


def _signal_from_evidence_id(evidence_id: str) -> SignalType | None:
    parts = evidence_id.split("/", 2)
    if len(parts) != 3:
        return None
    prefix = parts[2].partition("#")[0]
    if prefix in ("logs", "metrics", "traces"):
        return prefix  # type: ignore[return-value]
    return None


class F1ReportEvidenceResolver:
    """Resolve evidence IDs against pinned F1 bundles and F2 live follow-up artifacts.

    Never reads ground-truth directories. Missing, corrupt, or non-matching incident
    references stay unresolvable rather than inventing synthetic provenance.
    """

    def __init__(
        self,
        reports_dir: Path | str,
        incident_id: str,
        *,
        follow_up_dir: Path | str | None = None,
        fallback_services: list[str] | None = None,
        service_by_evidence_id: dict[str, str] | None = None,
    ):
        self.reports_dir = Path(reports_dir)
        self.incident_id = incident_id
        self.follow_up_dir = Path(follow_up_dir) if follow_up_dir else None
        self.fallback_services = list(fallback_services or [])
        self.service_by_evidence_id = dict(service_by_evidence_id or {})
        self._manifest_cache: dict[str, tuple[Path, dict]] = {}
        self._records_cache: dict[tuple[str, str], list[dict]] = {}
        self._live_cache: dict[str, dict] = {}

    def _load_manifest(self, snapshot_id: str) -> tuple[Path, dict] | None:
        if snapshot_id in self._manifest_cache:
            return self._manifest_cache[snapshot_id]
        try:
            from observa_alerts.evidence import load_manifest
            root, manifest = load_manifest(self.reports_dir, self.incident_id, snapshot_id)
        except Exception:
            return None
        self._manifest_cache[snapshot_id] = (root, manifest)
        return root, manifest

    def _load_ref_records(self, snapshot_id: str, ref_id: str) -> list[dict] | None:
        key = (snapshot_id, ref_id)
        if key in self._records_cache:
            return self._records_cache[key]
        loaded = self._load_manifest(snapshot_id)
        if loaded is None:
            return None
        try:
            from observa_alerts.evidence import read_evidence
            rows: list[dict] = []
            cursor = 0
            while cursor is not None:
                page = read_evidence(
                    self.reports_dir,
                    self.incident_id,
                    snapshot_id,
                    ref_id,
                    cursor=cursor,
                    limit=100,
                    max_bytes=65536,
                )
                if page.get("error"):
                    return None
                rows.extend(page.get("items", []))
                cursor = page.get("next_cursor")
        except Exception:
            return None
        self._records_cache[key] = rows
        return rows

    def _resolve_live_anchor(
        self,
        evidence_id: str,
        snapshot_id: str,
        signal: SignalType,
        anchor: str,
        window: IncidentWindow | None,
    ) -> EvidenceRef | None:
        if not self.follow_up_dir or not anchor.startswith("live-"):
            return None
        live_name = anchor.split(":", 1)[0]
        live_path = (self.follow_up_dir / f"{live_name}.json").resolve()
        try:
            if not live_path.is_relative_to(self.follow_up_dir.resolve()) or not live_path.is_file():
                return None
            if live_name not in self._live_cache:
                self._live_cache[live_name] = json.loads(live_path.read_bytes())
        except Exception:
            return None
        payload = self._live_cache[live_name]
        if payload.get("snapshot_id") and payload.get("snapshot_id") != snapshot_id:
            return None
        qw = payload.get("query_window") or {}
        start = parse_utc(qw.get("start")) or (window.start_time if window else None)
        end = parse_utc(qw.get("end")) or (window.end_time if window else None)
        if start is None:
            return None
        service = (
            payload.get("service")
            or self.service_by_evidence_id.get(evidence_id)
            or (self.fallback_services[0] if len(self.fallback_services) == 1 else None)
        )
        if not service:
            return None
        return EvidenceRef(
            evidence_id=evidence_id,
            signal_type=signal,
            service=service,
            source_record_ref=str(live_path),
            query_ref=payload.get("query"),
            start_time=start,
            end_time=end,
            availability_status="available",
            snapshot_id=snapshot_id,
        )

    def resolve(self, evidence_id: str) -> EvidenceRef | None:
        parts = evidence_id.split("/", 2)
        if len(parts) != 3 or parts[0] != self.incident_id or not parts[1]:
            return None
        snapshot_id = parts[1]
        ref_part, sep, anchor = parts[2].partition("#")
        if not sep or not anchor or ref_part not in ("logs", "metrics", "traces"):
            return None
        signal: SignalType = ref_part  # type: ignore[assignment]
        loaded = self._load_manifest(snapshot_id)
        if loaded is None:
            return None
        root, manifest = loaded
        qw = manifest.get("query_window") or {}
        win_start = parse_utc(qw.get("start"))
        win_end = parse_utc(qw.get("end"))
        if win_start is None:
            return None
        window = IncidentWindow(start_time=win_start, end_time=win_end)

        if anchor.startswith("live-"):
            return self._resolve_live_anchor(evidence_id, snapshot_id, signal, anchor, window)

        # Anchor formats used by F2 signal agents:
        # logs: #P1, #E1, #<idx>
        # metrics: #M1, #<idx>
        # traces: #T1, #<idx>, or trace-<id>#<idx>
        refs = manifest.get("refs") or {}
        if signal not in refs:
            return None
        ref_meta = refs[signal]
        service = self.service_by_evidence_id.get(evidence_id)

        # Try exact integer index lookup when anchor is numeric
        record_ts: datetime | None = None
        if anchor.isdigit():
            rows = self._load_ref_records(snapshot_id, signal)
            idx = int(anchor)
            if rows is None or idx >= len(rows):
                return None
            row = rows[idx]
            service = service or row.get("service.name") or row.get("service")
            record_ts = parse_utc(row.get("timestamp"))
        else:
            # F2 compact symbol anchors (#P1, #M1, #T1, #E1) reference retained manifest evidence
            rows = self._load_ref_records(snapshot_id, signal)
            if rows is None or len(rows) == 0:
                return None
            if not service:
                services = {
                    row.get("service.name") or row.get("service")
                    for row in rows
                    if row.get("service.name") or row.get("service")
                }
                if len(services) == 1:
                    service = next(iter(services))
                elif len(self.fallback_services) == 1:
                    service = self.fallback_services[0]
                else:
                    service = "unknown"

        return EvidenceRef(
            evidence_id=evidence_id,
            signal_type=signal,
            service=service or "unknown",
            source_record_ref=str(root / ref_meta["path"]),
            query_ref=f"{self.incident_id}/{snapshot_id}/{signal}#{anchor}",
            start_time=record_ts or win_start,
            end_time=win_end,
            availability_status="available",
            snapshot_id=snapshot_id,
        )
