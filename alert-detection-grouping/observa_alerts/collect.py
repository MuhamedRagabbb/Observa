"""Public query APIs only. No controller, ground-truth, SQL, or business mutations."""
import concurrent.futures
import json
import math
import re
import time
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from .config import SERVICES


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def get_json(base, path, params=None):
    url = base + path + ("?" + urlencode(params) if params else "")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(url, headers={"Accept": "application/json"}), timeout=5) as response:
        raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError("Response exceeds 8MB limit")
        return json.loads(raw)


def queries(config):
    scope = 'service_name=~"' + "|".join(SERVICES) + '",deployment_environment="' + config["environment"] + '"'
    def metric(name, extra=""):
        return name + "{" + scope + ("," + extra if extra else "") + "}"
    w = f'[{int(config["window_seconds"])}s]'
    def count(name, extra=""):
        return "sum by (service_name) (increase(" + metric(name, extra) + w + "))"
    requests = count("http_server_requests_total")
    zero = " or on(service_name) (0 * (" + requests + "))"
    errors = "(" + count("http_server_requests_total", 'http_status_code=~"5.."') + zero + ")"
    calls = count("dependency_calls_total")
    failures = "(" + count("dependency_calls_total", 'http_status_code=~"5..|0"') + zero + ")"
    return {
        "requests": requests,
        "http_errors": errors + " / (" + requests + ")",
        "latency": "histogram_quantile(0.95, sum by (le,service_name) (rate(" +
                   metric("http_server_duration_seconds_bucket") + w + ")))",
        "pool_pressure": "sum by (service_name) (" + metric("db_pool_active") + ") / sum by (service_name) (" + metric("db_pool_max") + ")",
        "pool_timeouts": "(" + count("db_pool_timeouts_total") + zero + ")",
        "payment_failures": "(" + count("payments_failed_total") + zero + ") / (" +
                            count("http_server_requests_total", 'http_route="/payments/charge"') + ")",
        "payment_requests": count("http_server_requests_total", 'http_route="/payments/charge"'),
        "dependency_requests": calls,
        "dependency_errors": failures + " / (" + calls + ")",
        "age": "time() - max by (service_name) (timestamp(" + metric("process_uptime_seconds") + "))",
        "uptime": "max by (service_name) (" + metric("process_uptime_seconds") + ")",
    }


SAFE_LOG_FIELDS = ("timestamp", "level", "service.name", "environment", "event", "trace_id",
                   "span_id", "request_id", "operation", "dependency", "http.route",
                   "http.status_code", "duration_ms", "error.type", "service.version", "deployment.id", "timeout")


def collect(config, now=None):
    now = time.time() if now is None else now
    snap = {"time": now, "environment": config["environment"], "origin": "live",
            "metrics": {}, "logs": [], "traces": [], "source_health": {}, "queries": queries(config)}
    def prom(item):
        key, query = item
        body = get_json(config["urls"]["prometheus"], "/api/v1/query", {"query": query, "time": now})
        if body.get("status") != "success" or body["data"]["resultType"] != "vector":
            raise ValueError("Invalid Prometheus vector")
        rows = {}
        for row in body["data"]["result"]:
            service = row["metric"].get("service_name")
            value = float(row["value"][1])
            if service in SERVICES:
                rows[service] = value if math.isfinite(value) else None
        return key, rows
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(prom, item): item[0] for item in snap["queries"].items()}
        for future, key in futures.items():
            try:
                _, rows = future.result()
                snap["metrics"][key] = rows
            except Exception as exc:
                snap["source_health"]["prometheus:" + key] = type(exc).__name__
    snap["source_health"]["prometheus"] = "ok" if not snap["source_health"] else "partial_or_unavailable"
    log_query = '{service_name=~"' + "|".join(SERVICES) + '"} | json | environment="' + config["environment"] + '" | level=~"ERROR|WARN" or event="release_changed"'
    snap["log_query"] = log_query
    try:
        body = get_json(config["urls"]["loki"], "/loki/api/v1/query_range", {
            "query": log_query, "start": str(int((now-config["window_seconds"])*1e9)),
            "end": str(int(now*1e9)), "limit": int(config["log_limit"]), "direction": "backward"})
        if body.get("status") != "success" or body["data"]["resultType"] != "streams":
            raise ValueError("Invalid Loki streams")
        count = 0
        for stream in body["data"]["result"]:
            for timestamp, line in stream["values"]:
                count += 1
                try:
                    log = json.loads(line)
                    if log.get("service.name") not in SERVICES or log.get("environment") != config["environment"]:
                        continue
                    entry = {k: v for k, v in log.items() if k in SAFE_LOG_FIELDS}
                    entry["observed_at"] = int(timestamp) / 1e9
                    snap["logs"].append(entry)
                except (ValueError, TypeError):
                    snap["source_health"]["loki_parse"] = "some_records_invalid"
        snap["source_health"]["loki"] = "truncated" if count >= config["log_limit"] else "ok"
    except Exception as exc:
        snap["source_health"]["loki"] = type(exc).__name__
    trace_ids = sorted({l.get("trace_id", "") for l in snap["logs"] if l.get("level") == "ERROR"})
    trace_ids = [t for t in trace_ids if re.fullmatch("[0-9a-f]{32}", t)][:int(config["trace_limit"])]
    snap["source_health"]["jaeger"] = "not_queried_no_error_trace_ids"
    for trace_id in trace_ids:
        try:
            body = get_json(config["urls"]["jaeger"], "/api/traces/" + trace_id)
            if not body.get("data"):
                snap["source_health"]["jaeger"] = "some_traces_missing"
                continue
            for trace in body["data"]:
                processes = trace.get("processes", {})
                spans = []
                for span in trace.get("spans", []):
                    service = processes.get(span.get("processID"), {}).get("serviceName")
                    if service not in SERVICES:
                        continue
                    tags = {t["key"]: t.get("value") for t in span.get("tags", [])}
                    spans.append({"span_id": span["spanID"], "service": service,
                                  "operation": span.get("operationName"), "duration_us": span.get("duration"),
                                  "start_us": span.get("startTime"), "error": tags.get("error") is True,
                                  "http_status_code": tags.get("http.status_code"),
                                  "references": span.get("references", [])})
                snap["traces"].append({"trace_id": trace_id, "spans": spans})
            if snap["source_health"]["jaeger"] == "not_queried_no_error_trace_ids":
                snap["source_health"]["jaeger"] = "ok"
        except Exception as exc:
            snap["source_health"]["jaeger"] = type(exc).__name__
    return snap
