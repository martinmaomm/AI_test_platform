"""只用于独立 Docker 网络内的低负载联调，不发布宿主机端口。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Fixture(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests_received = 0
    lock = threading.Lock()

    def do_GET(self):
        if self.path == "/probe":
            with self.lock:
                type(self).requests_received += 1
                count = type(self).requests_received
            payload, status = {"ok": True, "requests_received": count}, 200
        elif self.path == "/stats":
            with self.lock:
                count = type(self).requests_received
            payload, status = {"requests_received": count}, 200
        else:
            payload, status = {"error": "not found"}, 404
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Fixture).serve_forever()
