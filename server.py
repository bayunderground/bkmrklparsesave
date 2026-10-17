#!/usr/bin/env python3
"""bkmrklparsesave server: append extracted items to Markdown files.

Stdlib only. Binds to 127.0.0.1; NGINX proxies to it. No auth.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.environ.get("BKMRKLPARSESAVE_HOST", "127.0.0.1")
PORT = int(os.environ.get("BKMRKLPARSESAVE_PORT", "8765"))
DATA_DIR = Path(os.environ.get("BKMRKLPARSESAVE_DATA", "./data")).resolve()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "bkmrklparsesave"

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"ok": True})
        else:
            self.send_error_json(404, "not found")

    def do_POST(self):
        if self.path == "/save":
            self.send_error_json(501, "not implemented")
        else:
            self.send_error_json(404, "not found")

    def do_PUT(self):
        self.send_error_json(405, "method not allowed")

    def do_DELETE(self):
        self.send_error_json(405, "method not allowed")

    def do_PATCH(self):
        self.send_error_json(405, "method not allowed")

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json(status, {"ok": False, "error": message})

    def log_message(self, fmt, *args):
        # One line per response; journald picks it up from stderr.
        sys.stderr.write("%s %s\n" % (self.client_address[0], fmt % args))


def make_server(host=HOST, port=PORT):
    """Build the threaded HTTP server (bound; caller decides serve_forever)."""
    return ThreadingHTTPServer((host, port), Handler)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    srv = make_server()
    print(f"bkmrklparsesave listening on http://{HOST}:{PORT} data={DATA_DIR}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
