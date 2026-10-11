#!/usr/bin/env python3
"""
Tests for the application layer: nfc_api.py (shared by the server and the
in-browser engine) and server.py (local / desktop mode).

Run:  pytest -q tests/
"""

import json
import os
import sys
import threading
import urllib.error
import urllib.request

import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

import nfc_api                                                  # noqa: E402
import server                                                   # noqa: E402

ST = {"length": 46, "width": 20, "turns": 6, "trace": 0.24, "gap": 0.15}


# ------------------------------------------------------------------ nfc_api
def test_generate_matches_reference_example():
    with open(os.path.join(ROOT, "examples", "st_reference.json")) as fh:
        ref = json.load(fh)
    out = nfc_api.generate({"params": ref["parameters"]})
    assert out["ok"], out["errors"]
    assert out["results"]["L_uH"] == pytest.approx(ref["results"]["L_uH"], rel=1e-9)
    assert out["geometry"]["copper"], "no copper polygons"
    assert out["geometry"]["board"] is not None


def test_generate_accepts_form_strings_and_ignores_unknown_keys():
    out = nfc_api.generate({"params": {**{k: str(v) for k, v in ST.items()},
                                       "make_bridge": "true", "bogus": 1}})
    assert out["ok"]
    assert out["params"]["turns"] == 6 and out["params"]["make_bridge"] is True


@pytest.mark.parametrize("bad, fragment", [
    ({"length": "abc"}, "not a valid number"),
    ({"turns": 80}, "radial build-up"),
    ({"term_pos": "nowhere"}, "terminal position"),
    ({"shape": "hexagon"}, "unknown shape"),
    ({"shape": "circle", "width": 30}, "square outline"),
])
def test_generate_reports_bad_input(bad, fragment):
    out = nfc_api.generate({"params": {**ST, **bad}})
    assert not out["ok"]
    assert any(fragment in e for e in out["errors"]), out["errors"]


def test_results_are_strict_json():
    for shape in ("rect", "octagon", "circle"):
        p = {**ST, "shape": shape, "length": 30, "width": 30}
        json.dumps(nfc_api.generate({"params": p}), allow_nan=False)


def test_export_produces_all_files():
    out = nfc_api.export({"params": ST, "name": "../evil name"})
    assert out["ok"]
    assert out["name"] == "..evilname"
    assert out["files"]["dxf"].lstrip().startswith("0")
    assert out["files"]["kicad_mod"].startswith("(footprint") or \
        out["files"]["kicad_mod"].startswith("(module")
    assert json.loads(out["files"]["json"])["parameters"]["turns"] == 6


def test_auto_corner_within_range():
    out = nfc_api.auto_corner({"params": ST})
    assert out["ok"] and out["min"] < out["corner_r"] < out["max"]


def test_sweep_finds_target():
    out = nfc_api.sweep({"params": ST, "target_uH": 2.7})
    assert out["ok"]
    best = next(r for r in out["rows"] if r["turns"] == out["best"])
    assert all(abs(best["L_uH"] - 2.7) <= abs(r["L_uH"] - 2.7) for r in out["rows"])
    Ls = [r["L_uH"] for r in out["rows"]]
    assert Ls == sorted(Ls), "inductance should grow with turn count"


def test_dispatch_json_never_raises():
    assert json.loads(nfc_api.dispatch_json("/api/nope", "{}"))["ok"] is False
    assert json.loads(nfc_api.dispatch_json("/api/generate", "not json"))["ok"] is False
    assert json.loads(nfc_api.dispatch_json("/api/health", ""))["ok"] is True


# ------------------------------------------------------------------- server
@pytest.fixture(scope="module")
def base_url():
    httpd = server.make_server("127.0.0.1", 0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.read()


def _post(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def test_server_serves_interface_and_engine(base_url):
    for path in ("/", "/app.js", "/boot.js", "/styles.css", "/web/index.html",
                 "/nfc_spiral.py", "/nfc_api.py", "/examples/st_reference.json"):
        status, body = _get(base_url + path)
        assert status == 200 and body, path


@pytest.mark.parametrize("path", ["/server.py", "/tests/test_api_server.py",
                                  "/..%2fserver.py", "/web/../../server.py"])
def test_server_refuses_other_files(base_url, path):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base_url + path)
    assert e.value.code == 404


def test_server_api_roundtrip(base_url):
    assert json.loads(_get(base_url + "/api/health")[1])["ok"]
    out = _post(base_url + "/api/generate", {"params": ST})
    assert out["ok"] and out["results"]["L_uH"] > 0
    out = _post(base_url + "/api/export", {"params": ST, "name": "t"})
    assert "dxf" in out["files"]


def test_server_unknown_endpoint_is_404(base_url):
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base_url + "/api/nope", {})
    assert e.value.code == 404


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
