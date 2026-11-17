import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from logs_agent.llm import LLMClient, LLMError


class Handler(BaseHTTPRequestHandler):
    seen: dict = {}

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        Handler.seen = {"path": self.path, "auth": self.headers.get("Authorization"),
                        "body": json.loads(self.rfile.read(n))}
        if self.headers.get("Authorization") != "Bearer k":
            return self._send(401, {"error": "bad key"})
        self._send(200, {"choices": [{"message": {"content": "hello"}}]})

    def do_GET(self):
        Handler.seen = {"path": self.path}
        self._send(200, {"data": [{"id": "m2"}, {"id": "m1"}]})

    def log_message(self, *a):
        pass


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        cls.env.start()
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.env.stop()

    def test_complete_request_shape(self):
        c = LLMClient("k", "some-model", self.base)
        self.assertEqual(c.complete([{"role": "user", "content": "hi"}]), "hello")
        self.assertEqual(Handler.seen["path"], "/v1/chat/completions")
        self.assertEqual(Handler.seen["auth"], "Bearer k")
        self.assertEqual(Handler.seen["body"]["model"], "some-model")
        self.assertNotIn("temperature", Handler.seen["body"])

    def test_temperature_only_when_set(self):
        LLMClient("k", "m", self.base, temperature=0.0).complete([])
        self.assertEqual(Handler.seen["body"]["temperature"], 0.0)

    def test_list_models(self):
        self.assertEqual(LLMClient("k", None, self.base).list_models(), ["m1", "m2"])
        self.assertEqual(Handler.seen["path"], "/v1/models")

    def test_http_error_becomes_llm_error(self):
        with self.assertRaises(LLMError) as cm:
            LLMClient("wrong", "m", self.base).complete([])
        self.assertIn("401", str(cm.exception))


class EmptyAnswerTests(unittest.TestCase):
    def test_empty_content_is_an_llm_error(self):
        for content in (None, "", "   "):
            c = LLMClient("k", "m")
            c._request = lambda *a, **k: {"choices": [{"message": {"content": content}}]}
            with self.assertRaises(LLMError):
                c.complete([])


class EnvTests(unittest.TestCase):
    def test_missing_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(LLMError) as cm:
                LLMClient.from_env()
        self.assertIn("GROQ_API_KEY", str(cm.exception))

    def test_missing_model(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "k"}, clear=True):
            with self.assertRaises(LLMError) as cm:
                LLMClient.from_env()
            self.assertIn("LLM_MODEL", str(cm.exception))
            self.assertIsNone(LLMClient.from_env(require_model=False).model)

    def test_groq_is_the_default(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "k", "LLM_MODEL": "m"}, clear=True):
            c = LLMClient.from_env()
        self.assertEqual(c.base_url, "https://api.groq.com/openai/v1")
        self.assertEqual(c.api_key, "k")
        self.assertIsNone(c.temperature)

    def test_xai_key_alone_uses_xai_url(self):
        with patch.dict(os.environ, {"XAI_API_KEY": "k", "LLM_MODEL": "m"}, clear=True):
            self.assertEqual(LLMClient.from_env().base_url, "https://api.x.ai/v1")

    def test_base_url_override(self):
        env = {"GROQ_API_KEY": "k", "LLM_MODEL": "m", "LLM_BASE_URL": "http://localhost:9/v1"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(LLMClient.from_env().base_url, "http://localhost:9/v1")


if __name__ == "__main__":
    unittest.main()
