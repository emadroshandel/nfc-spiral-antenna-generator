#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NFC Spiral Antenna Generator -- local application server.

A small HTTP server from the Python standard library (no web framework). It
serves the browser interface from ./web and answers the JSON API from
nfc_api.py, which wraps nfc_spiral.py -- the same engine the command line and
the online version use.

    python server.py                 # start and open the default browser
    python server.py --port 8800     # choose the port
    python server.py --no-browser    # headless (e.g. on a lab server)

For a native window instead of a browser tab, run desktop.py.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import socket
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(BASE, "web")
sys.path.insert(0, BASE)

import nfc_api                                                 # noqa: E402

APP_VERSION = nfc_api.APP_VERSION

# Files outside ./web the page is allowed to fetch. The browser engine loads
# the two Python files when no server is present; the examples are presets.
ROOT_FILES = {"nfc_spiral.py", "nfc_api.py", "LICENSE"}
ROOT_DIRS = ("examples/", "docs/images/")

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("text/x-python", ".py")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/vnd.dxf", ".dxf")


def _inside(root: str, path: str) -> bool:
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    return path == root or path.startswith(root + os.sep)


def resolve_static(url_path: str):
    """Map a URL path to a file on disk, or None. Never escapes BASE."""
    rel = unquote(url_path).lstrip("/")
    if rel.startswith("web/"):
        rel = rel[4:]
    if rel in ("", "index.html"):
        return os.path.join(WEB, "index.html")
    cand = os.path.join(WEB, rel)
    if _inside(WEB, cand) and os.path.isfile(cand):
        return cand
    if rel in ROOT_FILES or rel.startswith(ROOT_DIRS):
        cand = os.path.join(BASE, rel)
        if _inside(BASE, cand) and os.path.isfile(cand):
            return cand
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "NFCSpiral/" + APP_VERSION

    def log_message(self, fmt, *args):            # keep the console readable
        if os.environ.get("NFC_SERVER_VERBOSE"):
            super().log_message(fmt, *args)

    # ---- plumbing ---------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, allow_nan=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _api(self, path: str, payload: dict):
        try:
            out = nfc_api.dispatch(path, payload)
        except KeyError:
            return self._json(404, {"ok": False,
                                    "errors": ["error: unknown endpoint " + path]})
        except Exception as exc:
            traceback.print_exc()
            return self._json(500, {"ok": False, "errors": [
                "error: %s: %s" % (type(exc).__name__, exc)]})
        self._json(200, out)

    # ---- verbs --------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self._api(path, {})
        f = resolve_static(path)
        if not f:
            return self._send(404, b"Not found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(f)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json",):
            ctype += "; charset=utf-8"
        with open(f, "rb") as fh:
            self._send(200, fh.read(), ctype)

    do_HEAD = do_GET

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._send(405, b"Method not allowed", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 5_000_000:
                raise ValueError("request too large")
            payload = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("expected a JSON object")
        except ValueError as exc:
            return self._json(400, {"ok": False, "errors": ["error: " + str(exc)]})
        self._api(path, payload)


def free_port(preferred: int = 8766, host: str = "127.0.0.1") -> int:
    """The preferred port if it is free, otherwise any free one."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free TCP port")


def make_server(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd


def main(argv=None):
    ap = argparse.ArgumentParser(description=nfc_api.APP_NAME + " - local server")
    ap.add_argument("--host", default="127.0.0.1",
                    help="interface to bind (0.0.0.0 to share on your network)")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)

    port = free_port(a.port, a.host)
    httpd = make_server(a.host, port)
    shown = "127.0.0.1" if a.host in ("0.0.0.0", "") else a.host
    url = f"http://{shown}:{port}/"
    print(f"{nfc_api.APP_NAME} {APP_VERSION}")
    print(f"  serving on {url}   (Ctrl+C to stop)")
    missing = [k for k, ok in nfc_api.meta()["dependencies"].items() if not ok]
    if missing:
        print("  note: missing optional packages: " + ", ".join(missing))
        print("        install with:  " + sys.executable + " -m pip install "
              + " ".join(missing))
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
