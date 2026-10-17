#!/usr/bin/env python3
"""bkmrklparsesave server: append extracted items to Markdown files.

Stdlib only. Binds to 127.0.0.1; NGINX proxies to it. No auth.
"""
import hashlib
import json
import math
import os
import re
import sys
import traceback
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.environ.get("BKMRKLPARSESAVE_HOST", "127.0.0.1")
PORT = int(os.environ.get("BKMRKLPARSESAVE_PORT", "8765"))
DATA_DIR = Path(os.environ.get("BKMRKLPARSESAVE_DATA", "./data")).resolve()

MAX_BODY = 5 * 1024 * 1024
MAX_ITEMS = 1000
RULE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def identifier_text(item):
    """Which text identifies this item, per the duplicate-detection contract.

    explicit id -> first field value -> canonical JSON of fields.
    Result is NFC-normalized and stripped; empty means "not usable".
    """
    fields = item["fields"]
    if item.get("id"):
        text = item["id"]
    else:
        first = next(iter(fields.values()))
        if isinstance(first, list):
            text = "\n".join(str(v) for v in first if v is not None)
        else:
            text = str(first)
    text = unicodedata.normalize("NFC", text).strip()
    if not text:
        text = json.dumps(
            fields, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        text = unicodedata.normalize("NFC", text).strip()
    return text


def marker_id(item):
    """16 hex chars of SHA-256 over the identifier text: never contains user text."""
    digest = hashlib.sha256(identifier_text(item).encode("utf-8")).hexdigest()
    return digest[:16]


class ValidationError(Exception):
    """Client mistake: answered with a 4xx JSON error, never a traceback."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def validate_rule_path(rule):
    """Rule name must be a plain slug resolving directly under DATA_DIR."""
    if not isinstance(rule, str) or not RULE_RE.fullmatch(rule):
        raise ValidationError(400, "invalid rule name")
    path = (DATA_DIR / (rule + ".md")).resolve()
    if path.parent != DATA_DIR:
        raise ValidationError(400, "invalid rule name")
    return path


def _bad_number(where):
    raise ValidationError(400, f"{where}: NaN/Infinity not allowed")


def _check_field_value(value, where):
    if value is None:
        return
    if isinstance(value, str):
        return
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            _bad_number(where)
        return
    if isinstance(value, list):
        for i, v in enumerate(value):
            if v is None:
                continue
            if not isinstance(v, (str, bool, int, float)):
                raise ValidationError(400, f"{where}: array items must be strings or numbers")
            if isinstance(v, float) and not math.isfinite(v):
                _bad_number(f"{where}[{i}]")
        return
    raise ValidationError(400, f"{where}: unsupported value type")


def validate_payload(obj):
    """Validate the /save JSON body per the contract; drop null field values."""
    if not isinstance(obj, dict):
        raise ValidationError(400, "body must be a JSON object")
    if "rule" not in obj:
        raise ValidationError(400, "missing 'rule'")
    validate_rule_path(obj["rule"])
    if "url" in obj and obj["url"] is not None and not isinstance(obj["url"], str):
        raise ValidationError(400, "'url' must be a string")
    items = obj.get("items")
    if not isinstance(items, list) or not items:
        raise ValidationError(400, "'items' must be a non-empty array")
    if len(items) > MAX_ITEMS:
        raise ValidationError(400, f"too many items (max {MAX_ITEMS})")
    cleaned = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValidationError(400, f"items[{i}] must be an object")
        item_id = item.get("id")
        if item_id is not None and not isinstance(item_id, str):
            raise ValidationError(400, f"items[{i}].id must be a string")
        fields = item.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise ValidationError(400, f"items[{i}].fields must be a non-empty object")
        kept = {}
        for name, value in fields.items():
            if not isinstance(name, str) or not name:
                raise ValidationError(400, f"items[{i}]: field names must be non-empty strings")
            if value is None:
                continue
            _check_field_value(value, f"items[{i}].fields.{name}")
            kept[name] = value
        if not kept:
            raise ValidationError(400, f"items[{i}].fields has no values after removing nulls")
        out = {"fields": kept}
        if item_id is not None and item_id.strip():
            out["id"] = item_id
        cleaned.append(out)
    payload = {"rule": obj["rule"], "items": cleaned}
    if isinstance(obj.get("url"), str):
        payload["url"] = obj["url"]
    return payload


def _reject_constant(token):
    raise ValueError(token)


def read_json_body(handler):
    """Read and parse the request body; raises ValidationError on any problem."""
    content_type = handler.headers.get("Content-Type", "")
    if "application/json" not in content_type.split(";")[0].strip().lower():
        raise ValidationError(415, "Content-Type must be application/json")
    length = handler.headers.get("Content-Length")
    if length is None:
        raise ValidationError(400, "Content-Length required")
    try:
        n = int(length)
    except ValueError:
        raise ValidationError(400, "bad Content-Length")
    if n > MAX_BODY:
        raise ValidationError(413, "body too large (max 5 MB)")
    raw = handler.rfile.read(n)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValidationError(400, "body is not valid UTF-8")
    try:
        # parse_constant rejects NaN/Infinity literals, which are not JSON.
        return json.loads(text, parse_constant=_reject_constant)
    except ValueError:
        raise ValidationError(400, "body is not valid JSON")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "bkmrklparsesave"

    def do_GET(self):
        self._run(self._get)

    def do_POST(self):
        self._run(self._post)

    def do_PUT(self):
        self._run(lambda: self.send_error_json(405, "method not allowed"))

    def do_DELETE(self):
        self._run(lambda: self.send_error_json(405, "method not allowed"))

    def do_PATCH(self):
        self._run(lambda: self.send_error_json(405, "method not allowed"))

    def _run(self, fn):
        """Every response is JSON; clients never see a traceback or a drop."""
        try:
            fn()
        except ValidationError as exc:
            self._reply(exc.status, {"ok": False, "error": exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            sys.stderr.write("internal error on %s %s\n" % (self.command, self.path))
            traceback.print_exc(file=sys.stderr)
            self._reply(500, {"ok": False, "error": "internal server error"})

    def _get(self):
        if self.path == "/health":
            self.send_json(200, {"ok": True})
        else:
            self.send_error_json(404, "not found")

    def _post(self):
        if self.path != "/save":
            self.send_error_json(404, "not found")
            return
        payload = validate_payload(read_json_body(self))
        self.send_error_json(501, "not implemented")

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reply(self, status, payload):
        """Send JSON, swallowing client-disconnect errors (nothing else can)."""
        try:
            self.send_json(status, payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

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
