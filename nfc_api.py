#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nfc_api.py -- the JSON interface behind the web / desktop application.

One small module, used two ways, so the two can never drift apart:

  * server mode   server.py imports it and answers POST /api/<name> over HTTP
                  (this is also what the native desktop window talks to)
  * browser mode  web/boot.js loads nfc_spiral.py and this file into Pyodide
                  on GitHub Pages and calls dispatch() directly

Every entry point takes a plain dict and returns a plain, JSON-safe dict.
Errors in the user's input come back as {"ok": False, "errors": [...]};
anything else raises and is reported by the caller.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, fields

import nfc_spiral as ns

APP_NAME = "NFC Spiral Antenna Generator"
APP_VERSION = "1.1.0"

# AntennaSpec field -> python type, taken from the defaults so a new field in
# nfc_spiral.py is picked up here without editing this file.
_DEFAULTS = ns.AntennaSpec()
_TYPES = {f.name: type(getattr(_DEFAULTS, f.name)) for f in fields(ns.AntennaSpec)}

_NAMED_TERM = ("right", "corner", "end", "center", "centre", "middle", "mid",
               "left", "start")


# --------------------------------------------------------------------------- #
#  helpers                                                                     #
# --------------------------------------------------------------------------- #
def _clean(v):
    """Make a value JSON-safe: NaN / inf become None, tuples become lists."""
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def _round(seq, n=4):
    return [[round(x, n), round(y, n)] for x, y in seq]


def spec_from(params: dict):
    """
    Build an AntennaSpec from loosely typed input (form strings, JSON numbers).
    Returns (spec, errors). Unknown keys are ignored; bad values are reported
    rather than raised, so the interface can show them next to the inputs.
    """
    errs = []
    kw = {}
    for key, raw in (params or {}).items():
        if key not in _TYPES:
            continue
        t = _TYPES[key]
        try:
            if t is bool:
                v = raw if isinstance(raw, bool) else str(raw).strip().lower() in (
                    "1", "true", "yes", "on")
            elif t is int:
                v = int(round(float(raw)))
            elif t is float:
                v = float(raw)
                if not math.isfinite(v):
                    raise ValueError
            else:
                v = str(raw).strip()
        except (TypeError, ValueError):
            errs.append(f"error: {key} = {raw!r} is not a valid number")
            continue
        kw[key] = v

    # term_pos may be a word or a coordinate; corner_r only means something
    # for the rounded rectangle.
    tp = str(kw.get("term_pos", _DEFAULTS.term_pos)).lower()
    if tp not in _NAMED_TERM:
        try:
            float(tp)
        except ValueError:
            errs.append(f"error: terminal position '{tp}' is not a number "
                        "or one of right / center / left")
            kw["term_pos"] = "right"
    if kw.get("shape", _DEFAULTS.shape) not in ("rect", "rrect", "octagon", "circle"):
        errs.append(f"error: unknown shape '{kw['shape']}'")
        kw["shape"] = "rect"
    if kw.get("model", _DEFAULTS.model) not in ("auto", "greenhouse", "mohan"):
        errs.append(f"error: unknown inductance model '{kw['model']}'")
        kw["model"] = "auto"
    if kw.get("shape", _DEFAULTS.shape) != "rrect":
        kw["corner_r"] = 0.0
    return ns.AntennaSpec(**kw), errs


def _hard(errs):
    return [e for e in errs if e.startswith("error:")]


# --------------------------------------------------------------------------- #
#  entry points                                                                #
# --------------------------------------------------------------------------- #
def health(_payload=None) -> dict:
    return {"ok": True, "app": APP_NAME, "version": APP_VERSION}


def meta(_payload=None) -> dict:
    """Defaults and version, so the interface never hard-codes them."""
    return {
        "ok": True,
        "app": APP_NAME,
        "version": APP_VERSION,
        "defaults": _clean(asdict(_DEFAULTS)),
        "dependencies": {k: bool(v) for k, v in ns.REQUIRED.items()},
    }


def _evaluate(sp):
    geo = ns.build_geometry(sp)
    res = ns.electrical(sp, geo)
    res["min_clearance_mm"] = ns.measure_clearance(geo.top_path, sp.trace, sp.gap)
    return geo, res


def generate(payload: dict) -> dict:
    """
    Geometry for the preview plus the electrical results.
    payload = {"params": {...AntennaSpec fields...}}
    """
    sp, errs = spec_from(payload.get("params", payload))
    errs += sp.validate()
    if _hard(errs):
        return {"ok": False, "errors": errs}

    geo, res = _evaluate(sp)
    clr = res["min_clearance_mm"]
    if clr == clr and clr < 0.98 * sp.gap:
        errs.append("warn: drawn clearance %.4f mm is below the requested gap "
                    "%g mm (polygon approximation of the curve)" % (clr, sp.gap))

    polys = [{"outer": _round(ext), "holes": [_round(h) for h in holes]}
             for ext, holes in ns.copper_polygons([(geo.top_path, sp.trace)])]

    hx, hy = sp.length / 2.0, sp.width / 2.0
    board = None
    if sp.board_margin >= 0:
        m = sp.board_margin
        y0 = -hy - sp.lead_out - sp.pad_h / 2.0 - m
        board = [[-hx - m, y0], [hx + m, y0], [hx + m, hy + m], [-hx - m, hy + m]]

    return _clean({
        "ok": True,
        "errors": errs,
        "params": asdict(sp),
        "results": res,
        "geometry": {
            "copper": polys,
            "centerline": _round(geo.top_path),
            "bridge": _round(geo.bridge),
            "pads": [list(geo.pad_outer)] + (
                [list(geo.pad_return)] if sp.make_bridge else []),
            "via": list(geo.via),
            "board": board,
            "keepout": [[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]],
            "trace": sp.trace,
            "pad_w": sp.pad_w, "pad_h": sp.pad_h,
            "via_pad": sp.via_pad, "via_drill": sp.via_drill,
        },
        "derived": {
            "pitch": sp.pitch,
            "build_up": sp.build_up,
            "term_x": sp.term_x,
            "term_x_min": sp.term_x_min,
            "term_x_max": sp.term_x_max,
            "corner_r_min": sp.min_corner_r_all_rounded,
            "corner_r_max": sp.max_corner_r,
            "inner_x": sp.length - 2 * sp.build_up,
            "inner_y": sp.width - 2 * sp.build_up,
        },
    })


def export(payload: dict) -> dict:
    """
    The downloadable files as text: DXF, KiCad footprint and JSON.
    payload = {"params": {...}, "name": "nfc_antenna"}
    """
    sp, errs = spec_from(payload.get("params", {}))
    errs += sp.validate()
    if _hard(errs):
        return {"ok": False, "errors": errs}
    name = "".join(c for c in str(payload.get("name") or "nfc_antenna")
                   if c.isalnum() or c in "-_.") or "nfc_antenna"

    geo, res = _evaluate(sp)
    files = {}
    with tempfile.TemporaryDirectory() as tmp:
        if ns.HAVE_EZDXF:
            p = ns.write_dxf(sp, geo, res, os.path.join(tmp, name + ".dxf"))
            with open(p, encoding="utf-8", errors="replace") as fh:
                files["dxf"] = fh.read()
        else:
            errs.append("warn: DXF skipped - the ezdxf package is not installed")
        p = ns.write_kicad_mod(sp, geo, name, os.path.join(tmp, name + ".kicad_mod"))
        with open(p, encoding="utf-8") as fh:
            files["kicad_mod"] = fh.read()
    files["json"] = json.dumps(_clean({"parameters": asdict(sp), "results": res,
                                       "warnings": errs}), indent=2)
    return {"ok": True, "name": name, "errors": errs, "files": files}


def auto_corner(payload: dict) -> dict:
    """Mid-range corner radius that rounds every turn without clipping."""
    params = dict(payload.get("params", payload))
    params["shape"] = "rrect"
    params["corner_r"] = 1.0                    # placeholder, recomputed below
    sp, errs = spec_from(params)
    lo, hi = sp.min_corner_r_all_rounded, sp.max_corner_r
    if hi <= 0 or _hard(errs):
        return {"ok": False, "errors": errs or ["error: outline too small to round"]}
    return {"ok": True, "corner_r": round(0.5 * (lo + hi), 3),
            "min": round(lo, 3), "max": round(hi, 3)}


def sweep(payload: dict) -> dict:
    """
    Inductance against turn count at fixed outline / trace / gap, plus the N
    closest to an optional target. payload = {"params": {...}, "target_uH": 2.5}
    """
    base, errs = spec_from(payload.get("params", {}))
    if _hard(errs):
        return {"ok": False, "errors": errs}
    target = payload.get("target_uH")
    try:
        target = None if target in (None, "") else float(target)
    except (TypeError, ValueError):
        return {"ok": False, "errors": ["error: target inductance is not a number"]}

    rows = []
    for n in range(1, 61):
        trial = ns.AntennaSpec(**{**asdict(base), "turns": n})
        if _hard(trial.validate()):
            break
        geo = ns.build_geometry(trial)
        r = ns.electrical(trial, geo)
        rows.append({"turns": n, "L_uH": r["L_uH"], "R_ac_ohm": r["R_ac_ohm"],
                     "Q": r["Q"], "C_total_resonant_pF": r["C_total_resonant_pF"],
                     "C_external_pF": r.get("C_external_pF")})
    if not rows:
        return {"ok": False, "errors": ["error: not even one turn fits this outline"]}
    best = None
    if target is not None:
        best = min(rows, key=lambda r: abs(r["L_uH"] - target))["turns"]
    return _clean({"ok": True, "rows": rows, "best": best, "target_uH": target})


ROUTES = {
    "health": health,
    "meta": meta,
    "generate": generate,
    "export": export,
    "auto_corner": auto_corner,
    "sweep": sweep,
}


def dispatch(path: str, payload: dict | None = None) -> dict:
    """Route '/api/<name>' (or just '<name>') to its handler."""
    name = str(path).rstrip("/").rsplit("/", 1)[-1]
    if name not in ROUTES:
        raise KeyError(name)
    return ROUTES[name](payload or {})


def dispatch_json(path: str, payload_json: str) -> str:
    """String in, string out -- the form Pyodide finds easiest to call."""
    try:
        out = dispatch(path, json.loads(payload_json or "{}"))
    except KeyError:
        out = {"ok": False, "errors": ["error: unknown endpoint " + str(path)]}
    except Exception as exc:                          # shown in the interface
        out = {"ok": False, "errors": ["error: %s: %s" % (type(exc).__name__, exc)]}
    return json.dumps(out, allow_nan=False)
