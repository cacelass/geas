"""Servidor HTTP falso para probar clientes LLM (§43) sin red real.

Imita las respuestas de OpenAI /chat/completions y Anthropic /messages.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeLLMServer:
    """Servidor HTTP que responde como OpenAI y Anthropic según la ruta."""

    def __init__(self):
        self.requests: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 (API de http.server)
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                headers = {k.lower(): v for k, v in self.headers.items()}
                server.requests.append({"path": self.path, "body": body, "headers": headers})
                if self.path == "/chat/completions":
                    payload = {
                        "choices": [
                            {"message": {"content": "Respuesta OpenAI", "role": "assistant"}}
                        ],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
                    }
                elif self.path == "/messages":
                    payload = {
                        "content": [{"type": "text", "text": "Respuesta Anthropic"}],
                        "usage": {"input_tokens": 9, "output_tokens": 4},
                    }
                else:
                    payload = {"error": "not found"}
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):  # silenciar logs del server
                pass

        server = self
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
