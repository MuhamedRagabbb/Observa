"""Read bounded follow-up evidence from the incident's telemetry window."""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from observa_alerts.collect import get_json, queries
from observa_alerts.config import SERVICES


def _utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class LiveEvidence:
    def __init__(self, directory, loki, prometheus, fetch=get_json):
        self.directory = Path(directory)
        self.loki = loki
        self.prometheus = prometheus
        self.fetch = fetch

    def lookup(self, signal, task, question):
        doc = task.incident
        window = doc.get("handoff", {}).get("query_window") or {}
        if doc.get("origin") != "live" or not window.get("start") or not window.get("end"):
            return []
        start, end = (datetime.fromisoformat(window[k].replace("Z", "+00:00")).timestamp()
                      for k in ("start", "end"))
        if end <= start:
            return []
        # Scope comes from the incident. Free-form objectives never become query syntax.
        named = [s for s in SERVICES if s in question.casefold()]
        services = named or [doc.get("anchor", {}).get("service")]
        rows = []
        for service in services:
            if service not in SERVICES or service not in doc.get("affected_services", []):
                continue
            env = doc["environment"]
            pool = bool(re.search(r"\bpool\b|db_pool|pool_timeouts|pool_pressure", question, re.I))
            if signal == "logs":
                observations = self.logs(service, env, start, end, pool)
            else:
                observations = self.metrics(service, env, start, end, pool, doc)
            for item in observations:
                item.update(signal=signal, service=service, incident_id=doc["id"],
                            snapshot_id=task.snapshot_id, window=window)
                body = json.dumps(item, sort_keys=True, allow_nan=False).encode()
                key = "live-" + hashlib.sha256(body).hexdigest()
                self.directory.mkdir(parents=True, exist_ok=True)
                path = self.directory / (key + ".json")
                if not path.exists():
                    with NamedTemporaryFile(dir=self.directory, delete=False) as stream:
                        stream.write(body)
                    Path(stream.name).replace(path)
                rows.append({"id": key, "service": service, "indicator": item["indicator"], "claim": item["claim"]})
        return rows

    def logs(self, service, env, start, end, pool):
        expr = ('{service_name=' + json.dumps(service) + '} | json | environment=' + json.dumps(env)
                + (' | event="db_pool_timeout"' if pool else ' | level=~"ERROR|WARN"'))
        body = self.fetch(self.loki, "/loki/api/v1/query_range", dict(
            query=expr, start=int(start * 1e9), end=int(end * 1e9), limit=100, direction="backward"))
        if body.get("status") != "success" or body["data"].get("resultType") != "streams":
            raise ValueError("Loki did not return log streams")
        groups = defaultdict(list)
        for stream in body["data"]["result"]:
            for ns, line in stream["values"]:
                log = json.loads(line)
                if log.get("service.name") != service or log.get("environment") != env:
                    continue
                event = log.get("event")
                if not event or (pool and event != "db_pool_timeout"):
                    continue
                groups[event, log.get("error.type"), log.get("http.status_code")].append(
                    {"timestamp_ns": ns, "trace_id": log.get("trace_id")})
        results = []
        for (event, error, status), records in groups.items():
            stamp = _utc(min(int(r["timestamp_ns"]) for r in records) / 1e9)
            detail = " ".join(str(v) for v in [error, f"HTTP {status}" if status else None] if v)
            results.append(dict(source="Loki", query=expr, indicator=event, records=records,
                                claim=f"{service}: {len(records)} {event} log records ({detail}) from {stamp}."))
        return results

    def samples(self, expr, start, end):
        body = self.fetch(self.prometheus, "/api/v1/query_range", dict(
            query=expr, start=start, end=end, step=max(3, math.ceil((end - start) / 300))))
        if body.get("status") != "success" or body["data"].get("resultType") != "matrix":
            raise ValueError("Prometheus did not return time series")
        series = body["data"]["result"]
        if len(series) > 1:
            raise ValueError("Expected one service time series")
        return {float(t): float(v) for t, v in series[0]["values"] if math.isfinite(float(v))} if series else {}

    def metrics(self, service, env, start, end, pool, doc):
        labels = '{service_name=' + json.dumps(service) + ',deployment_environment=' + json.dumps(env) + '}'
        if pool:
            exprs = [f"sum({name}{labels})" for name in ("db_pool_active", "db_pool_max")]
            active, capacity = (self.samples(expr, start, end) for expr in exprs)
            paired = [(t, active[t], capacity[t]) for t in sorted(active.keys() & capacity.keys()) if capacity[t] > 0]
            if not paired:
                return []
            t, used, total = max(paired, key=lambda row: row[1] / row[2])
            return [dict(source="Prometheus", queries=exprs, indicator="pool_pressure", samples=paired,
                         claim=f"{service} used {used:g} of {total:g} database connections at {_utc(t)} "
                               f"(highest sampled utilization in the window: {used / total:.0%}).")]
        if not re.fullmatch(r"[\w.-]+", env):
            return []
        definitions = queries({"window_seconds": 60, "environment": env})
        rules = sorted({a.get("rule") for a in doc["alerts"].values() if a["service"] == service
                        and a.get("rule") in definitions})
        results = []
        for rule in rules:
            expr = f'({definitions[rule]}) and on(service_name) process_uptime_seconds{labels}'
            values = self.samples(expr, start, end)
            if values:
                t = max(values, key=values.get)
                results.append(dict(source="Prometheus", query=expr, indicator=rule, samples=sorted(values.items()),
                                    claim=f"{service}: peak {rule} was {values[t]:g} at {_utc(t)}."))
        return results
