"""Real loopback HTTP transport with synthetic Prometheus/Loki/Jaeger responses."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from observa_alerts.collect import collect, queries
from observa_alerts.config import settings
from observa_alerts.demo import snapshot
from observa_alerts.engine import Engine


class HttpPipelineTests(unittest.TestCase):
    def test_collect_detect_group_handoff_over_http(self):
        config = settings()
        fixture = {"snapshot": snapshot(config,1000,"payments"), "paths": []}
        by_query={query:key for key,query in queries(config).items()}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                parsed=urlparse(self.path)
                fixture["paths"].append(parsed.path)
                s=fixture["snapshot"]
                if parsed.path=="/api/v1/query":
                    key=by_query[parse_qs(parsed.query)["query"][0]]
                    data={"status":"success","data":{"resultType":"vector","result":[
                        {"metric":{"service_name":service},"value":[s["time"],str(value)]}
                        for service,value in s["metrics"][key].items()]}}
                elif parsed.path=="/loki/api/v1/query_range":
                    data={"status":"success","data":{"resultType":"streams","result":[
                        {"stream":{"service_name":"orders-service"},"values":[
                            [str(int(s["time"]*1e9)),json.dumps(log)] for log in s["logs"]]}]}}
                elif parsed.path=="/api/traces/"+"a"*32:
                    data={"data":[{"traceID":"a"*32,"processes":{"p":{"serviceName":"payments-service"}},
                                  "spans":[{"spanID":"1","processID":"p","operationName":"POST /payments/charge",
                                            "duration":12000,"startTime":int(s["time"]*1e6),
                                            "tags":[{"key":"error","value":True},{"key":"http.status_code","value":503}]}]}]}
                else:
                    self.send_error(404)
                    return
                raw=json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        config["urls"]={key:f"http://127.0.0.1:{server.server_port}" for key in config["urls"]}
        try:
            with tempfile.TemporaryDirectory() as d:
                engine=Engine(d,config)
                try:
                    for t in (1000,1010,1020):
                        fixture["snapshot"]=snapshot(config,t,"payments")
                        observations=collect(config,t)
                        self.assertEqual(observations["source_health"]["prometheus"],"ok")
                        self.assertEqual(observations["source_health"]["loki"],"ok")
                        incidents=engine.tick(observations)
                    self.assertEqual(len(incidents),1)
                    self.assertEqual(set(incidents[0]["affected_services"]),{"api-gateway","orders-service","payments-service"})
                    self.assertTrue(incidents[0]["evidence"]["traces"])
                    self.assertFalse(any("fault" in p or "control" in p for p in fixture["paths"]))
                finally:
                    engine.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
