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
import threading
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
MARKER_RE = re.compile(r"^<!-- item-id: ([0-9a-f]{16}) -->$", re.MULTILINE)

# One process: read-ids -> filter -> append must not interleave between requests.
SAVE_LOCK = threading.Lock()


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


def sanitize_text(text):
    """User text must never forge an HTML comment marker."""
    return text.replace("<!--", "&lt;!--")


def render_scalar(value):
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render_title(fields):
    """Title = first field, first line, collapsed, <=100 chars, '#' stripped."""
    first = next(iter(fields.values()))
    if isinstance(first, list):
        raw = ", ".join(
            render_scalar(v) for v in first if not isinstance(v, str) or v.strip()
        )
    else:
        raw = render_scalar(first)
    line = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n", 1)[0]
    line = " ".join(line.split()).lstrip("#").strip()[:100].strip()
    return line or "Item"


def render_field(label, value):
    """Render one field as markdown lines; blank means the field is omitted."""
    safe_label = sanitize_text(" ".join(str(label).replace(":", " ").split()))
    safe_label = safe_label or "Field"
    if isinstance(value, list):
        parts = [sanitize_text(render_scalar(v)) for v in value if v is not None]
        parts = [p for p in parts if p.strip()]
        if not parts:
            return []
        out = ["- %s:" % safe_label]
        for part in parts:
            lines = part.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            out.append("  - " + lines[0])
            for extra in lines[1:]:
                # 4-space continuation keeps text below the bullet, never column 0.
                out.append("    " + extra)
        return out
    text = sanitize_text(render_scalar(value)).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if not any(line.strip() for line in lines):
        return []
    if len(lines) == 1:
        return ["- %s: %s" % (safe_label, lines[0].strip())]
    out = ["- %s:" % safe_label]
    for line in lines:
        out.append("  " + line if line.strip() else "  ")
    return out


def render_section(item, marker):
    """One item -> one markdown section ending with '---'."""
    fields = item["fields"]
    lines = ["## %s" % sanitize_text(render_title(fields)), ""]
    lines.append("<!-- item-id: %s -->" % marker)
    lines.append("")
    for label, value in fields.items():
        lines.extend(render_field(label, value))
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def existing_ids(path):
    """All marker IDs already present in the target file."""
    if not path.is_file():
        return set()
    return set(MARKER_RE.findall(path.read_text(encoding="utf-8")))


def append_text(path, chunk):
    """Append chunk, ending the file with exactly one newline; durable write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        if path.stat().st_size > 0:
            with open(path, "rb") as probe:
                probe.seek(-1, os.SEEK_END)
                if probe.read(1) != b"\n":
                    fh.write("\n")
            fh.write("\n")  # blank line between old content and the new section
        fh.write(chunk)
        fh.flush()
        os.fsync(fh.fileno())


def save_items(payload):
    """Dedupe against the file and append new sections. Returns (path, n, skipped)."""
    path = validate_rule_path(payload["rule"])
    with SAVE_LOCK:
        ids = existing_ids(path)
        sections = []
        for item in payload["items"]:
            mid = marker_id(item)
            if mid in ids:
                continue
            ids.add(mid)  # also skips duplicates within the same request
            sections.append(render_section(item, mid))
        if sections:
            append_text(path, "\n\n".join(sections) + "\n")
    return path, len(sections), len(payload["items"]) - len(sections)


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


def _reject_before_body(handler, status, message, drain):
    """Reject before consuming the body: drain a bounded amount, then close,
    so the client still streaming its request can receive the error."""
    handler.close_connection = True
    if drain:
        try:
            handler.rfile.read(drain)
        except OSError:
            pass
    raise ValidationError(status, message)


def read_json_body(handler):
    """Read and parse the request body; raises ValidationError on any problem."""
    length = handler.headers.get("Content-Length")
    if length is None:
        raise ValidationError(400, "Content-Length required")
    try:
        n = int(length)
    except ValueError:
        handler.close_connection = True
        raise ValidationError(400, "bad Content-Length")
    content_type = handler.headers.get("Content-Type", "")
    if "application/json" not in content_type.split(";")[0].strip().lower():
        _reject_before_body(handler, 415, "Content-Type must be application/json", n)
    if n > MAX_BODY:
        # Drain at most a small multiple of the limit; the rest hits a closed
        # socket — that is what the limit is for.
        _reject_before_body(handler, 413, "body too large (max 5 MB)", min(n, MAX_BODY * 2))
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

    def send_error(self, code, message=None, explain=None):
        """Protocol errors and unsupported methods answer JSON, never HTML."""
        self.close_connection = True
        # This API has two endpoints: any method it lacks is simply not allowed.
        if code == 501:
            code, message = 405, "method not allowed"
        self.send_json(code, {"ok": False, "error": message or "error"})

    def _run(self, fn):
        """Every response is JSON; clients never see a traceback or a drop."""
        try:
            fn()
        except ValidationError as exc:
            self.send_error_json(exc.status, exc.message)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            sys.stderr.write("internal error on %s %s\n" % (self.command, self.path))
            traceback.print_exc(file=sys.stderr)
            self.send_error_json(500, "internal server error")

    def _get(self):
        if self.path == "/health":
            self.send_json(200, {"ok": True})
        elif self.path == "/save":
            self.send_error_json(405, "method not allowed")
        else:
            self.send_error_json(404, "not found")

    def _post(self):
        if self.path != "/save":
            if self.path == "/health":
                self.send_error_json(405, "method not allowed")
            else:
                self.send_error_json(404, "not found")
            return
        payload = validate_payload(read_json_body(self))
        path, saved, skipped = save_items(payload)
        self.send_json(
            200,
            {
                "ok": True,
                "rule": payload["rule"],
                "file": path.name,
                "saved": saved,
                "skipped": skipped,
            },
        )

    def send_json(self, status, payload):
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass  # client disconnected; nothing left to answer

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
