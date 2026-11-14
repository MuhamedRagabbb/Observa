"""Deterministic rules, conservative evidence-based grouping, durable lifecycle."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .config import SERVICES


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def fingerprint(*parts):
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:20]


METRIC_RULES = {
    "http_errors": ("generic", "HTTP 5xx ratio", "ratio"),
    "latency": ("generic", "HTTP p95 duration", "seconds"),
    "pool_pressure": ("database", "Database pool utilization", "ratio"),
    "pool_timeouts": ("database", "Database acquire timeouts", "count_per_window"),
    "payment_failures": ("payment", "Payment failure ratio including declines", "ratio"),
    "dependency_errors": ("generic", "Dependency 5xx or transport failure ratio", "ratio"),
}


def observations(snapshot, config):
    now = snapshot["time"]
    metrics = snapshot["metrics"]
    result = []
    for source in ("prometheus", "loki"):
        health = snapshot["source_health"].get(source, "missing")
        result.append({"rule": "source_unavailable", "service": "observability:" + source,
                       "family": "telemetry", "title": "Telemetry source degraded or unavailable",
                       "value": 0 if health == "ok" else 1, "unit": "boolean", "threshold": 0,
                       "verdict": "good" if health == "ok" else "bad",
                       "evidence": {"source": source, "status": health, "observed_at": utc(now)}})
    for service in SERVICES:
        age = metrics.get("age", {}).get(service)
        fresh = age is not None and 0 <= age <= config["freshness_seconds"]
        result.append({"rule": "service_signal_missing", "service": service,
                       "family": "telemetry", "title": "Service telemetry missing or not progressing",
                       "value": age, "unit": "seconds", "threshold": config["freshness_seconds"],
                       "verdict": "unknown" if snapshot["source_health"].get("prometheus") != "ok"
                                  else "good" if fresh else "bad",
                       "evidence": {"source": "prometheus", "query": snapshot["queries"].get("uptime"),
                                    "note": "Missing telemetry is not proof that the service is down."}})
        for rule, (family, title, unit) in METRIC_RULES.items():
            if rule.startswith("pool_") and service == "api-gateway":
                continue
            if rule == "payment_failures" and service != "payments-service":
                continue
            if rule == "dependency_errors" and service not in ("orders-service", "api-gateway"):
                continue
            value = metrics.get(rule, {}).get(service)
            volume_key = {"payment_failures": "payment_requests", "dependency_errors": "dependency_requests"}.get(rule, "requests")
            volume = metrics.get(volume_key, {}).get(service)
            known = fresh and value is not None
            if rule in ("http_errors", "latency", "payment_failures", "dependency_errors"):
                known = known and volume is not None and volume >= config["min_requests"]
            result.append({"rule": rule, "service": service, "family": family, "title": title,
                           "value": value, "unit": unit, "threshold": config["thresholds"][rule],
                           "verdict": "unknown" if not known else "bad" if value > config["thresholds"][rule] else "good",
                           "evidence": {"source": "prometheus", "query": snapshot["queries"].get(rule),
                                        "window_end": utc(now), "window_seconds": config["window_seconds"],
                                        "request_count": volume, "sample_age_seconds": age}})
        for event, rule, family in (("order_persistence_failed", "persistence_errors", "regression"),
                                   ("unexpected_exception", "new_exception", "exception")):
            records = [x for x in snapshot["logs"] if x.get("service.name") == service and x.get("event") == event
                       and now-config["window_seconds"] <= x.get("observed_at", now) <= now]
            # Count distinct event records, not repeated polls. Error type retained in evidence.
            count = len({json.dumps(x, sort_keys=True) for x in records})
            complete = snapshot["source_health"].get("loki") == "ok" and fresh
            result.append({"rule": rule, "service": service, "family": family, "title": event,
                           "value": count, "unit": "events_per_window", "threshold": config["thresholds"][rule],
                           "verdict": "bad" if count > config["thresholds"][rule] else "good" if complete else "unknown",
                           "evidence": {"source": "loki", "query": snapshot.get("log_query"),
                                        "window_end": utc(now), "window_seconds": config["window_seconds"],
                                        "records": records[:20]}})
    return result


def relation(alert, incident, snapshot, config):
    """Do not merge two independently anchored incidents through a generic bridge."""
    if snapshot["time"] - incident["last_observed_epoch"] > config["group_window_seconds"]:
        return None
    anchor = incident["anchor"]
    if alert["service"] == anchor["service"]:
        if (alert["family"] == "telemetry") != (anchor["family"] == "telemetry"):
            return None
        if alert["family"] == anchor["family"] or alert["family"] == "generic" or anchor["family"] == "generic":
            return {"kind": "same_service_symptoms", "confidence": "moderate",
                    "related_service": anchor["service"],
                    "explanation": "Overlapping observations in one service; shared cause is unconfirmed."}
        return None
    if alert["family"] != "generic":
        return None
    linked_services = set(incident["affected_services"])
    witnesses = [l for l in snapshot["logs"] if l.get("service.name") == alert["service"]
                 and l.get("dependency") in linked_services and l.get("event") == "dependency_request_failed"
                 and (l.get("timeout") is True or l.get("level") == "ERROR" or int(l.get("http.status_code", 0)) >= 500)]
    if witnesses:
        return {"kind": "failed_dependency", "confidence": "high",
                "related_service": witnesses[0]["dependency"],
                "explanation": "Upstream service logged a failed call to a service already in this incident. Causation remains a hypothesis.",
                "direction": {"upstream": alert["service"], "downstream": witnesses[0]["dependency"]},
                "witnesses": witnesses[:5]}
    own = set()
    other = set()
    for trace in snapshot["traces"]:
        failed = {s["service"] for s in trace["spans"] if s.get("error") or (s.get("http_status_code") or 0) >= 500}
        if alert["service"] in failed:
            own.add(trace["trace_id"])
        if failed & linked_services:
            other.add(trace["trace_id"])
    common = own & other
    if common:
        related = sorted({s["service"] for t in snapshot["traces"] if t["trace_id"] in common
                          for s in t["spans"] if s["service"] in linked_services
                          and (s.get("error") or (s.get("http_status_code") or 0) >= 500)})
        return {"kind": "shared_failed_trace", "confidence": "high",
                "related_service": related[0],
                "explanation": "Both services have failed spans in the same request trace; not a proven root cause.",
                "trace_ids": sorted(common)}
    return None


class Engine:
    def __init__(self, data_dir, config):
        self.config = config
        self.directory = Path(data_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "state.sqlite3", timeout=10)
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
        row = self.db.execute("SELECT body FROM state WHERE id=1").fetchone()
        self.state = json.loads(row[0]) if row else {"last_poll": 0, "alerts": {}, "incidents": {}}

    def close(self):
        self.db.close()

    def tick(self, snapshot):
        now = snapshot["time"]
        if snapshot["environment"] != self.config["environment"]:
            raise ValueError("Snapshot environment mismatch")
        if now <= self.state["last_poll"]:
            raise ValueError("Duplicate or out-of-order snapshot")
        gap = self.state["last_poll"] and now-self.state["last_poll"] > 2*self.config["poll_seconds"] + 5
        self.state["last_poll"] = now
        progress = self.state.setdefault("uptime_progress", {})
        for service, value in snapshot["metrics"].get("uptime", {}).items():
            previous = progress.get(service)
            if previous is None or previous["value"] != value:
                progress[service] = {"value": value, "changed": now}
            else:
                age = snapshot["metrics"].get("age", {}).get(service)
                snapshot["metrics"].setdefault("age", {})[service] = max(age or 0, now-previous["changed"])
        seen = set()
        for obs in observations(snapshot, self.config):
            key = fingerprint(snapshot["environment"], obs["service"], obs["rule"])
            seen.add(key)
            alert = self.state["alerts"].get(key)
            if alert is None:
                if obs["verdict"] != "bad":
                    continue
                alert = {"id": key, "status": "pending", "first_observed_epoch": now,
                         "bad_since": now, "good_since": None, "incident_id": None, "history": []}
                self.state["alerts"][key] = alert
            before = alert["status"]
            if gap:
                alert["bad_since"] = None
                alert["good_since"] = None
            alert.update(obs)
            alert["data_quality"] = obs["verdict"]
            if obs["verdict"] == "bad":
                if before == "resolved":
                    alert.update(first_observed_epoch=now, bad_since=now, incident_id=None, history=[])
                    alert.pop("resolved_at", None)
                    alert.pop("detected_at", None)
                    alert.pop("trigger_evidence", None)
                    alert.pop("peak_value", None)
                if alert["bad_since"] is None:
                    alert["bad_since"] = now
                alert["good_since"] = None
                alert["last_observed_epoch"] = now
                alert.setdefault("trigger_evidence", json.loads(json.dumps(obs)))
                if obs["value"] is not None:
                    alert["peak_value"] = max(alert.get("peak_value", obs["value"]), obs["value"])
                alert["severity"] = "critical" if (
                    obs["rule"] in ("http_errors", "dependency_errors", "payment_failures") and obs["value"] >= .5
                ) else "warning"
                if now-alert["bad_since"] >= self.config["pending_seconds"]:
                    alert["status"] = "firing"
                    alert.setdefault("detected_at", utc(now))
                else:
                    alert["status"] = "firing" if before == "firing" else "pending"
            elif obs["verdict"] == "good":
                alert["bad_since"] = None
                if before == "pending":
                    alert["status"] = "resolved"
                    alert["resolved_at"] = utc(now)
                elif before == "firing":
                    if alert["good_since"] is None:
                        alert["good_since"] = now
                    if now-alert["good_since"] >= self.config["recovery_seconds"]:
                        alert["status"] = "resolved"
                        alert["resolved_at"] = utc(now)
            else:
                alert["bad_since"] = None
                alert["good_since"] = None
            if before != alert["status"]:
                alert["history"].append({"at": utc(now), "from": before, "to": alert["status"]})
        active = [a for a in self.state["alerts"].values() if a["status"] == "firing"]
        # Specific signals become anchors first. Gateway processed after Orders for propagated errors.
        active.sort(key=lambda a: (a["family"] == "generic", a["service"] == "api-gateway", a["id"]))
        for alert in active:
            if alert["incident_id"]:
                continue
            chosen = None
            reasons = None
            for incident in sorted(self.state["incidents"].values(), key=lambda i: i["id"]):
                if incident["status"] == "resolved":
                    continue
                reason = relation(alert, incident, snapshot, self.config)
                if reason and (reasons is None or reason["confidence"] == "high"):
                    chosen, reasons = incident, reason
            if chosen is None:
                ident = "INC-" + fingerprint(alert["id"], alert["first_observed_epoch"])
                chosen = {"schema_version": "1.0", "id": ident, "revision": 0, "origin": snapshot["origin"],
                          "environment": snapshot["environment"], "status": "open",
                          "created_at": utc(now), "first_observed_at": utc(alert["first_observed_epoch"]),
                          "last_observed_epoch": alert["last_observed_epoch"],
                          "anchor": {"service": alert["service"], "family": alert["family"]},
                          "anchor_alert_id": alert["id"],
                          "affected_services": [], "alerts": {}, "relationships": [],
                          "timeline": [{"at": utc(now), "event": "incident_opened"}],
                          "evidence": {"logs": [], "traces": []}, "root_cause": {"status": "not_determined"},
                          "summary": "Observed " + alert["family"] + " symptoms in " + alert["service"]}
                self.state["incidents"][ident] = chosen
            else:
                # Link to the service actually named by the evidence, not always
                # to the original anchor (e.g. Gateway -> Orders -> Payments).
                members = list(chosen["alerts"].values()) + [
                    a for a in self.state["alerts"].values() if a["incident_id"] == chosen["id"]]
                related = sorted(
                    (a for a in members if a["service"] == reasons["related_service"]),
                    key=lambda a: (a["id"] != chosen["anchor_alert_id"], a["id"]),
                )
                chosen["relationships"].append({"alert_id": alert["id"],
                                                "related_alert_id": related[0]["id"], **reasons})
                if chosen["anchor"]["family"] == "generic" and alert["family"] != "generic":
                    chosen["anchor"] = {"service": alert["service"], "family": alert["family"]}
                    chosen["anchor_alert_id"] = alert["id"]
                    chosen["summary"] = "Observed " + alert["family"] + " symptoms in " + alert["service"]
            alert["incident_id"] = chosen["id"]
            if alert["service"] not in chosen["affected_services"]:
                chosen["affected_services"].append(alert["service"])
            chosen["timeline"].append({"at": utc(now), "event": "alert_attached", "alert_id": alert["id"]})
        for incident in self.state["incidents"].values():
            if incident["status"] == "resolved":
                continue
            attached = [a for a in self.state["alerts"].values() if a["incident_id"] == incident["id"]]
            for a in attached:
                incident["alerts"][a["id"]] = json.loads(json.dumps(a))
            if attached:
                incident["last_observed_epoch"] = max(a["last_observed_epoch"] for a in attached)
            if attached and all(a["status"] == "resolved" for a in attached):
                incident["status"] = "resolved"
                incident["resolved_at"] = utc(now)
                incident["timeline"].append({"at": utc(now), "event": "recovery_confirmed"})
            incident["source_health"] = snapshot["source_health"]
            incident["updated_at"] = utc(now)
            incident["severity"] = "critical" if any(a.get("severity") == "critical" for a in attached) else "warning"
            incident["last_observed_at"] = utc(incident["last_observed_epoch"])
            incident["revision"] += 1
            incident["data_gaps"] = [k + ": " + v for k, v in snapshot["source_health"].items()
                                     if v not in ("ok", "not_queried_no_error_trace_ids")]
            incident["data_gaps"] += [a["rule"] + " on " + a["service"] + ": insufficient current evidence"
                                      for a in attached if a["data_quality"] == "unknown"]
            for kind in ("logs", "traces"):
                records = snapshot[kind]
                if kind == "logs":
                    records = [l for l in records if l.get("service.name") in incident["affected_services"]
                               and l.get("event") != "request_received"
                               and (l.get("level") in ("ERROR", "WARN") or l.get("event") == "release_changed")]
                else:
                    records = [t for t in records if any(s["service"] in incident["affected_services"] for s in t["spans"])]
                combined = {json.dumps(x, sort_keys=True): x for x in incident["evidence"][kind]+records}
                limit = 100 if kind == "logs" else 20
                incident["evidence"][kind] = list(combined.values())[-limit:]
            incident["handoff"] = {
                "ready_for_investigation": True,
                "query_window": {"start": utc(datetime.fromisoformat(incident["first_observed_at"].replace("Z", "+00:00")).timestamp()-120),
                                 "end": utc(now)},
                "sources": self.config["urls"],
                "next_steps": ["Check failed dependency directions against span parent relationships.",
                               "Compare metrics with a healthy baseline and check release_changed events.",
                               "Distinguish business payment declines from service unavailability.",
                               "Validate suspected initiating service; grouping is not proof of causality."],
                "evidence_limits": "Bounded samples: 100 sanitized logs and 20 traces. Original Jaeger traces may expire.",
                "hypotheses": [{"service": incident["anchor"]["service"],
                                "statement": "Candidate focus for investigation based on the first grouped specific signal.",
                                "confidence": "unconfirmed"}],
            }
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (1,?)", (json.dumps(self.state, allow_nan=False),))
        return list(self.state["incidents"].values())
