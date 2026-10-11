#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NFC Spiral Antenna Generator -- desktop mode.

Starts the local server (server.py) on a background thread and shows the
interface in a native application window using pywebview. Downloads go
through a native "Save as" dialog. If pywebview is not installed the
application opens in the default web browser instead, so this entry point
always works:

    python desktop.py
    pip install pywebview      # optional, for the native window

The classic Tkinter interface is still available as nfc_spiral_gui.py.
"""

from __future__ import annotations

import os
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

# A windowed (no-console) build has no stdout/stderr; give tracebacks somewhere to go.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w"))

import server                                                 # noqa: E402


class DesktopAPI:
    """Exposed to the page as window.pywebview.api."""

    def __init__(self):
        self.window = None
        if getattr(sys, "frozen", False):          # packaged .exe: BASE is a temp dir
            docs = os.path.join(os.path.expanduser("~"), "Documents")
            self.last_dir = docs if os.path.isdir(docs) else os.path.expanduser("~")
        else:
            self.last_dir = os.path.join(BASE, "output")

    def save_file(self, filename: str, content: str, binary_b64: bool = False):
        """Ask where to save, then write. Returns the path, or '' if cancelled."""
        import webview                                        # type: ignore
        os.makedirs(self.last_dir, exist_ok=True)
        ext = os.path.splitext(filename)[1].lstrip(".") or "txt"
        dialog = getattr(webview, "FileDialog", None)
        kind = dialog.SAVE if dialog else webview.SAVE_DIALOG
        picked = self.window.create_file_dialog(
            kind, directory=self.last_dir, save_filename=filename,
            file_types=(f"{ext.upper()} file (*.{ext})", "All files (*.*)"))
        if not picked:
            return ""
        path = picked if isinstance(picked, str) else picked[0]
        if binary_b64:
            import base64
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(content))
        else:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
        self.last_dir = os.path.dirname(path)
        return path

    def open_folder(self, path: str = ""):
        """Show a saved file's folder in Explorer / Finder / the file manager."""
        folder = os.path.dirname(path) if path and os.path.isfile(path) else (
            path or self.last_dir)
        if sys.platform.startswith("win"):
            os.startfile(folder)                              # type: ignore
        elif sys.platform == "darwin":
            os.system(f'open "{folder}"')
        else:
            os.system(f'xdg-open "{folder}" >/dev/null 2>&1 &')
        return True


def main():
    port = server.free_port(8766)
    httpd = server.make_server("127.0.0.1", port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.3)
    url = f"http://127.0.0.1:{port}/"
    print(f"{server.nfc_api.APP_NAME} {server.APP_VERSION} - {url}")

    try:
        import webview                                        # type: ignore
    except ImportError:
        print("pywebview is not installed - opening the default browser instead.")
        print("For a native desktop window:  " + sys.executable
              + " -m pip install pywebview")
        import webbrowser
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            httpd.shutdown()
        return

    api = DesktopAPI()
    api.window = webview.create_window(
        f"{server.nfc_api.APP_NAME} {server.APP_VERSION}", url, js_api=api,
        width=1440, height=920, min_size=(980, 640))
    try:
        webview.start()
    finally:
        httpd.shutdown()
    sys.exit(0)


if __name__ == "__main__":
    main()
