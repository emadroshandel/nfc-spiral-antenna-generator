#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 nfc_spiral.py  --  Parametric planar NFC / RFID spiral antenna generator
================================================================================

Generates a planar spiral coil (rectangular / rounded-rectangular / octagonal /
circular), computes its inductance with a segment-based Greenhouse model, and
exports clean DXF geometry ready to import into any ECAD tool
(KiCad, Altium, Eagle, OrCAD, EasyEDA, PADS, ...).

Outputs
-------
  <name>.dxf         layered DXF R2010:
                       ANT_CENTERLINE  polyline with constant width  (trace path)
                       ANT_COPPER      closed copper outline polygons (top layer)
                       ANT_COPPER_HATCH solid hatch of the copper (optional)
                       ANT_BOTTOM      bottom-layer return bridge
                       ANT_PADS        terminal pads / via land
                       ANT_KEEPOUT     coil bounding box
                       BOARD_OUTLINE   optional board rectangle
                       ANT_INFO        parameter + result annotation text
  <name>.kicad_mod   ready-to-place KiCad footprint (traces on F.Cu + 2 SMD pads)
  <name>.json        all parameters and electrical results
  <name>.png         quick preview render (needs matplotlib)

Electrical model
----------------
  * Greenhouse / Grover partial-inductance summation:
        L = sum(L_self,i) + sum(+/- M_ij)
    Self term uses the geometric-mean-distance of a w x t rectangular bar
    (GMD = 0.2235*(w+t)); mutual term uses the exact closed form for two
    parallel filaments of arbitrary axial overlap. Exact for orthogonal
    (rectangular) spirals -- the same approach ST's NFC inductance tool uses.
  * Mohan current-sheet expression reported as an independent cross-check.
  * Skin-effect-corrected DC/AC resistance, Q, and the resonating capacitance
    for the target carrier (13.56 MHz by default), including chip capacitance.

Usage
-----
  # ST reference example (46 x 20 mm, 6 turns, 0.24 mm trace, 0.15 mm gap)
  python3 nfc_spiral.py --length 46 --width 20 --turns 6 \
                        --trace 0.24 --gap 0.15 --out st_ref

  # Same coil with concentric rounded corners (radius shrinks one pitch per
  # turn, so the turn-to-turn gap stays constant through the corner)
  python3 nfc_spiral.py --length 46 --width 20 --turns 6 \
                        --trace 0.24 --gap 0.15 --corner-r auto --out st_round

  # Circular 40 mm coil, 5 turns, chip C = 50 pF
  python3 nfc_spiral.py --shape circle --length 40 --width 40 --turns 5 \
                        --trace 0.5 --gap 0.3 --chip-cap 50 --out round40

  # Import as a library
  from nfc_spiral import AntennaSpec, build
  res = build(AntennaSpec(length=46, width=20, turns=6, trace=0.24, gap=0.15),
              outdir=".", name="ant")

Requires:  ezdxf  (mandatory)  |  shapely (copper outline)  |  matplotlib (preview)
           pip install ezdxf shapely matplotlib
Units:     millimetres everywhere in the API and in the DXF ($INSUNITS = mm).
================================================================================
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import List, Tuple, Optional

# ------------------------------------------------------------------ constants
MU0 = 4.0e-7 * math.pi          # H/m
RHO_CU = 1.724e-8               # ohm*m  @20 C
Pt = Tuple[float, float]

# --------------------------------------------------------------------- optional deps
try:
    import ezdxf
    from ezdxf import units as dxf_units
    HAVE_EZDXF = True
except ImportError:                                   # pragma: no cover
    HAVE_EZDXF = False

try:
    from shapely.geometry import LineString, MultiPolygon, Polygon
    from shapely.ops import unary_union
    HAVE_SHAPELY = True
except ImportError:                                   # pragma: no cover
    HAVE_SHAPELY = False

try:
    import matplotlib                                 # noqa: F401
    HAVE_MPL = True
except ImportError:                                   # pragma: no cover
    HAVE_MPL = False

REQUIRED = {"ezdxf": HAVE_EZDXF, "shapely": HAVE_SHAPELY, "matplotlib": HAVE_MPL}


def dependency_report() -> str:
    """Human-readable status of the optional/required packages, with the fix."""
    lines = ["", "  Python interpreter in use:", f"    {sys.executable}",
             "", "  Packages:"]
    for pkg, ok in REQUIRED.items():
        need = {"ezdxf": "required for DXF export",
                "shapely": "copper outline + clearance check",
                "matplotlib": "PNG preview"}[pkg]
        lines.append(f"    [{'OK  ' if ok else 'MISS'}] {pkg:<12} {need}")
    missing = [p for p, ok in REQUIRED.items() if not ok]
    if missing:
        lines += [
            "",
            "  Install them into THIS interpreter (not just 'pip install ...',",
            "  which may target a different Python):",
            "",
            f'    "{sys.executable}" -m pip install {" ".join(missing)}',
            "",
            "  Or let this script do it for you:",
            "",
            f'    "{sys.executable}" "{os.path.abspath(__file__)}" --install-deps',
            "",
            "  In VS Code, make sure the interpreter shown above is the one",
            "  selected in the status bar (Ctrl+Shift+P -> Python: Select",
            "  Interpreter), then restart the terminal.",
        ]
    return "\n".join(lines)


def install_deps() -> int:
    """pip-install the missing packages into the running interpreter."""
    import subprocess
    missing = [p for p, ok in REQUIRED.items() if not ok]
    if not missing:
        print("All dependencies are already present.")
        return 0
    print(f"Installing {', '.join(missing)} into {sys.executable} ...\n")
    rc = subprocess.call([sys.executable, "-m", "pip", "install", *missing])
    if rc == 0:
        print("\nDone. Re-run your command.")
    else:
        print("\npip failed. Try adding --user, or run the terminal as "
              "administrator:\n"
              f'  "{sys.executable}" -m pip install --user {" ".join(missing)}')
    return rc


# ============================================================================ #
#  1.  SPECIFICATION                                                           #
# ============================================================================ #
@dataclass
class AntennaSpec:
    """Every dimension is in millimetres, angles in degrees."""

    # --- geometry -----------------------------------------------------------
    shape: str = "rect"          # rect | rrect | octagon | circle
    length: float = 46.0         # outer copper extent in X  (antenna length)
    width: float = 20.0          # outer copper extent in Y  (antenna width)
    turns: int = 6               # number of turns
    trace: float = 0.24          # conductor width
    gap: float = 0.15            # edge-to-edge spacing between turns
    corner_r: float = 0.0        # centreline corner radius (shape='rrect')
    thickness_um: float = 35.0   # copper thickness (35 um = 1 oz)

    # --- terminals ----------------------------------------------------------
    term_pos: str = "right"      # where the terminal leaves the bottom edge:
                                 #   'right' | 'center' | 'left' | a number (mm)
    pad_side: str = "auto"       # which side the return pad sits: auto|left|right
    lead_out: float = 2.0        # length the outer terminal sticks out (mm)
    pad_w: float = 1.2           # terminal pad width  (X)
    pad_h: float = 1.2           # terminal pad height (Y)
    pad_pitch: float = 2.0       # centre distance between the two pads
    via_pad: float = 0.9         # inner via land diameter
    via_drill: float = 0.4       # inner via drill diameter
    make_bridge: bool = True     # draw the bottom-layer return trace

    # --- board / output -----------------------------------------------------
    board_margin: float = -1.0   # >=0 draws a board outline this far outside
    hatch_copper: bool = True    # add a solid hatch inside the copper outline
    arc_segments: int = 18       # segments per 90 deg of arc / rounded corner
    circle_segments: int = 120   # samples per turn for shape='circle'

    # --- electrical ---------------------------------------------------------
    freq_mhz: float = 13.56      # carrier frequency
    chip_cap_pf: float = 0.0     # tag/reader IC input capacitance (pF)
    include_leads: bool = False  # count the terminal leads in the L model
    model: str = "auto"          # auto | greenhouse | mohan
    proximity_factor: float = 1.0  # multiplies R_ac (1.3-2.5 for tight pitch)

    # ------------------------------------------------------------------ derived
    @property
    def pitch(self) -> float:
        """Centre-to-centre spacing of adjacent turns, edge gap + conductor."""
        return self.trace + self.gap

    @property
    def sides_per_turn(self) -> int:
        """Facets used per turn (orthogonal shapes are not drawn from facets)."""
        if self.shape == "octagon":
            return 8
        if self.shape == "circle":
            return max(8, int(self.circle_segments))
        return 0

    @property
    def radial_pitch(self) -> float:
        """
        Pitch measured along the radius for the polygonal shapes.

        A polygon inscribed on a spiral has its *edges* closer together than its
        vertices: the chord midpoint sits at r*cos(pi/n), so a radial step of
        `pitch` only buys pitch*cos(pi/n) of edge-to-edge spacing. An octagon
        loses 8% of the gap that way, which is the difference between passing
        and failing a 0.15 mm rule. Divide it back out so the *drawn* clearance
        is the clearance that was asked for.
        """
        n = self.sides_per_turn
        return self.pitch if n == 0 else self.pitch / math.cos(math.pi / n)

    @property
    def build_up(self) -> float:
        """Radial thickness of the whole winding, one side."""
        return self.trace + (self.turns - 1) * self.radial_pitch

    @property
    def thickness(self) -> float:            # mm
        return self.thickness_um * 1e-3

    # ---- terminal position ------------------------------------------------
    @property
    def term_x_max(self) -> float:
        """Rightmost terminal x: the outer turn's own right edge."""
        return self.length / 2.0 - self.trace / 2.0

    @property
    def term_x_min(self) -> float:
        """
        Leftmost terminal x. The break steps left by one pitch per turn and the
        inner end needs one more pitch of clearance, so the staircase consumes
        (turns + 1) pitches before it would run off the innermost left edge.
        """
        ax, _ = _half_extents(self, self.turns - 1)
        return -ax + (self.turns + 1) * self.pitch

    @property
    def term_x(self) -> float:
        """Resolve term_pos to an x coordinate on the bottom edge, clamped."""
        key = str(self.term_pos).strip().lower()
        if key in ("right", "corner", "end"):
            x = self.term_x_max
        elif key in ("center", "centre", "middle", "mid"):
            x = 0.0
        elif key in ("left", "start"):
            x = self.term_x_min
        else:
            x = float(self.term_pos)
        return max(self.term_x_min, min(self.term_x_max, x))

    @property
    def inner_end_x(self) -> float:
        """
        Where the innermost turn stops (and the via goes). One pitch clear of
        the last break so the clearance to it is exactly `gap`; pulled toward
        the middle of the window when there is room.
        """
        ax, _ = _half_extents(self, self.turns - 1)
        limit = self.term_x - self.turns * self.pitch
        return max(-ax + self.pitch, min(0.0, limit))

    @property
    def max_corner_r(self) -> float:
        """
        Largest outer-turn corner radius that still leaves straight runs on
        every edge of every turn. Bounded by the innermost turn's half-extent
        (its own radius is corner_r - (N-1)*pitch, which must fit inside it).
        """
        ax, ay = _half_extents(self, self.turns - 1)
        return max(0.0, min(ax, ay) + (self.turns - 1) * self.pitch)

    @property
    def min_corner_r_all_rounded(self) -> float:
        """
        Smallest corner_r that still rounds every turn. Radii shrink inwards by
        one pitch per turn, so the innermost turn only stays rounded if the
        outer radius starts at least (N-1)*pitch above zero.
        """
        return (self.turns - 1) * self.pitch

    def validate(self) -> List[str]:
        """
        Return a list of human-readable problems ([] == geometry is legal).
        Each message is prefixed 'error: ' (not buildable) or 'warn: '
        (buildable, but you should know about it).
        """
        errs: List[str] = []
        if self.turns < 1:
            errs.append("error: turns must be >= 1")
        if min(self.trace, self.gap) <= 0:
            errs.append("error: trace width and gap must be > 0")
        build_up = self.build_up
        for name, dim in (("length", self.length), ("width", self.width)):
            if 2 * build_up >= dim:
                errs.append(
                    f"error: {self.turns} turns need {build_up:.3f} mm of radial build-up on "
                    f"each side, but the antenna {name} is only {dim:.3f} mm "
                    f"(max turns for this {name} ~ "
                    f"{int((dim / 2 + self.gap) // self.pitch)})")
            elif dim - 2 * build_up < 2 * self.pitch:
                errs.append(
                    f"warn: inner window along {name} is only "
                    f"{dim - 2 * build_up:.3f} mm - very tight, consider fewer turns")
        try:
            requested = self.term_x if str(self.term_pos).strip().lower() in (
                "right", "corner", "end", "center", "centre", "middle", "mid",
                "left", "start") else float(self.term_pos)
        except ValueError:
            errs.append(f"error: term_pos '{self.term_pos}' is not a number or "
                        "one of right / center / left")
            requested = self.term_x
        if not (self.term_x_min - 1e-9 <= requested <= self.term_x_max + 1e-9):
            errs.append(
                f"warn: term_pos x={requested:g} mm is outside the feasible "
                f"range [{self.term_x_min:.2f}, {self.term_x_max:.2f}] mm for "
                f"{self.turns} turns - clamped to {self.term_x:.2f} mm")

        if self.shape == "rrect":
            if self.corner_r <= 0:
                errs.append("error: shape='rrect' needs corner_r > 0")
            else:
                r_max = self.max_corner_r
                if self.corner_r > r_max:
                    errs.append(
                        f"warn: corner_r {self.corner_r:g} mm exceeds the largest "
                        f"radius that fits this outline ({r_max:.2f} mm) - "
                        f"the outer arcs will be clipped to the straight runs")
                r_min = self.min_corner_r_all_rounded
                if self.corner_r < r_min:
                    n_sharp = int(math.ceil((r_min - self.corner_r) / self.pitch))
                    errs.append(
                        f"warn: corner_r {self.corner_r:g} mm shrinks to zero before the "
                        f"innermost turn - the last {n_sharp} turn(s) keep sharp "
                        f"corners; use corner_r between {r_min:.2f} and "
                        f"{r_max:.2f} mm to round every turn")
        if self.shape == "circle" and abs(self.length - self.width) > 1e-9:
            errs.append("error: shape='circle' uses length as the diameter; "
                        "length and width should be equal")
        if self.shape in ("circle", "octagon") and min(self.length,
                                                       self.width) > 0:
            aspect = max(self.length, self.width) / min(self.length, self.width)
            if aspect > 1.02:
                errs.append(
                    f"error: shape='{self.shape}' needs a square outline "
                    f"(aspect {aspect:.2f}). Concentric circles/octagons only "
                    f"keep a constant gap when they are not stretched - an "
                    f"elongated one pinches the clearance. Use shape='rrect' "
                    f"with a large corner_r for an elongated rounded coil.")
        return errs


# ============================================================================ #
#  2.  GEOMETRY                                                                #
# ============================================================================ #
def _half_extents(sp: AntennaSpec, i: int) -> Tuple[float, float]:
    """Centreline half-extents of turn *i* (0 = outermost)."""
    off = sp.trace / 2.0 + i * sp.pitch
    return sp.length / 2.0 - off, sp.width / 2.0 - off


def _rect_spiral(sp: AntennaSpec, with_radii: bool = False):
    """
    Orthogonal spiral centreline, counter-clockwise, spiralling inwards.

    With `with_radii=True` also returns the per-vertex fillet radius list for
    _fillet(): turn i gets corner_r - i*pitch so every corner arc of the coil
    is concentric with the ones outside it, and the step-in corner gets a small
    radius bounded by the pitch.  Radii that would go negative come back as 0
    (that corner stays sharp) and are reported by AntennaSpec.validate().

    The spiral is broken on the bottom edge at x = term_x, which is where the
    outer terminal comes out. The break steps left by one pitch per turn, so
    the step-in segments form a staircase instead of stacking into a vertical
    bar that would short every turn together. Moving term_x therefore moves the
    whole staircase with the pad, which is what lets the terminal sit anywhere
    along the bottom edge rather than only at the corner.
    """
    pts: List[Pt] = []
    radii: List[float] = []
    p = sp.pitch
    xt = sp.term_x
    step_r = min(sp.corner_r, 0.45 * p) if sp.corner_r > 0 else 0.0
    for i in range(sp.turns):
        ax, ay = _half_extents(sp, i)
        s_in = xt - i * p              # where this turn is entered, on the bottom
        s_out = xt - (i + 1) * p       # where it breaks to step inwards
        r_i = max(0.0, sp.corner_r - i * p)
        #      entry      BR         TR        TL          BL
        pts += [(s_in, -ay), (ax, -ay), (ax, ay), (-ax, ay), (-ax, -ay)]
        radii += [step_r if i else 0.0, r_i, r_i, r_i, r_i]
        if i < sp.turns - 1:
            pts.append((s_out, -ay))   # run back along the bottom, stop short
            radii.append(step_r)       # then step in vertically to turn i+1
    ax, ay = _half_extents(sp, sp.turns - 1)
    pts.append((sp.inner_end_x, -ay))  # inner end, one pitch clear of the break
    radii.append(0.0)
    return (pts, radii) if with_radii else pts


def _polar_spiral(sp: AntennaSpec, n_sides: Optional[int]) -> List[Pt]:
    """
    Archimedean spiral. n_sides=None -> smooth circle, else a regular polygon
    (e.g. 8 for an octagon). Anisotropic scaling maps it onto length x width.
    """
    rx0 = sp.length / 2.0 - sp.trace / 2.0
    ry0 = sp.width / 2.0 - sp.trace / 2.0
    total = 2.0 * math.pi * sp.turns
    per_turn = sp.circle_segments if n_sides is None else n_sides
    n = max(8, int(per_turn * sp.turns))
    pts: List[Pt] = []
    # start at angle -90 deg (bottom) so the terminal exits downwards
    for k in range(n + 1):
        t = total * k / n
        shrink = sp.radial_pitch * t / (2.0 * math.pi)
        a = -math.pi / 2.0 + t
        rx, ry = rx0 - shrink, ry0 - shrink
        if n_sides is not None:            # polygonise: keep the corner radius
            seg = 2 * math.pi / n_sides
            corr = math.cos(seg / 2.0) / math.cos(((a % seg) + seg) % seg - seg / 2.0)
            rx, ry = rx / corr, ry / corr
        pts.append((rx * math.cos(a), ry * math.sin(a)))
    return pts


def _fillet(pts: List[Pt], r, seg_per_90: int) -> List[Pt]:
    """
    Replace interior corners of a polyline with tangent arcs.

    `r` is either a single radius applied to every corner, or a per-vertex list
    the same length as `pts` (0 = leave that corner sharp).  The per-vertex form
    is what makes a *concentric* spiral fillet possible: each turn needs its own
    radius, one pitch smaller than the turn outside it, so that all four corner
    arcs of the coil share a common centre and the turn-to-turn gap stays
    constant through the corner instead of opening out to pitch*sqrt(2).
    """
    per_vertex = not isinstance(r, (int, float))
    if not per_vertex and r <= 0:
        return pts
    if len(pts) < 3:
        return pts
    radii = list(r) if per_vertex else [float(r)] * len(pts)
    out: List[Pt] = [pts[0]]
    for i in range(1, len(pts) - 1):
        r = radii[i]
        if r <= 0:
            out.append(pts[i])
            continue
        p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
        v1 = (p0[0] - p1[0], p0[1] - p1[1])
        v2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1 = math.hypot(*v1)
        l2 = math.hypot(*v2)
        if l1 < 1e-12 or l2 < 1e-12:
            continue
        u1 = (v1[0] / l1, v1[1] / l1)
        u2 = (v2[0] / l2, v2[1] / l2)
        cosang = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        ang = math.acos(cosang)                       # interior angle at p1
        if ang > math.pi - 1e-6 or ang < 1e-6:
            out.append(p1)
            continue
        tan_len = r / math.tan(ang / 2.0)
        tan_len = min(tan_len, 0.49 * l1, 0.49 * l2)  # never eat a whole segment
        rr = tan_len * math.tan(ang / 2.0)
        t1 = (p1[0] + u1[0] * tan_len, p1[1] + u1[1] * tan_len)
        t2 = (p1[0] + u2[0] * tan_len, p1[1] + u2[1] * tan_len)
        bis = (u1[0] + u2[0], u1[1] + u2[1])
        bl = math.hypot(*bis)
        if bl < 1e-12:
            out.append(p1)
            continue
        d_c = rr / math.sin(ang / 2.0)
        c = (p1[0] + bis[0] / bl * d_c, p1[1] + bis[1] / bl * d_c)
        a1 = math.atan2(t1[1] - c[1], t1[0] - c[0])
        a2 = math.atan2(t2[1] - c[1], t2[0] - c[0])
        da = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        nseg = max(2, int(seg_per_90 * abs(da) / (math.pi / 2)))
        out.append(t1)
        for k in range(1, nseg):
            a = a1 + da * k / nseg
            out.append((c[0] + rr * math.cos(a), c[1] + rr * math.sin(a)))
        out.append(t2)
    out.append(pts[-1])
    # drop duplicates
    ded: List[Pt] = [out[0]]
    for p in out[1:]:
        if math.dist(p, ded[-1]) > 1e-9:
            ded.append(p)
    return ded


@dataclass
class Geometry:
    coil: List[Pt]                       # spiral centreline only
    outer_lead: List[Pt]                 # top-layer stub to the outer pad
    inner_stub: List[Pt]                 # top-layer stub to the via
    bridge: List[Pt] = field(default_factory=list)   # bottom-layer return
    pad_outer: Pt = (0.0, 0.0)
    pad_return: Pt = (0.0, 0.0)
    via: Pt = (0.0, 0.0)

    @property
    def top_path(self) -> List[Pt]:
        """Full continuous top-copper centreline: outer pad -> coil -> via."""
        p = list(self.outer_lead)
        for q in self.coil:
            if not p or math.dist(q, p[-1]) > 1e-9:
                p.append(q)
        for q in self.inner_stub:
            if math.dist(q, p[-1]) > 1e-9:
                p.append(q)
        return p


def build_geometry(sp: AntennaSpec) -> Geometry:
    if sp.shape in ("rect", "rrect"):
        if sp.shape == "rrect":
            pts, radii = _rect_spiral(sp, with_radii=True)
            # a terminal hard against the right edge makes the first run
            # zero-length; drop it so the filleter sees a clean polyline
            keep = [0] + [i for i in range(1, len(pts))
                          if math.dist(pts[i], pts[i - 1]) > 1e-9]
            coil = _fillet([pts[i] for i in keep], [radii[i] for i in keep],
                           sp.arc_segments)
        else:
            coil = _rect_spiral(sp)
    elif sp.shape == "octagon":
        coil = _polar_spiral(sp, 8)
    elif sp.shape == "circle":
        coil = _polar_spiral(sp, None)
    else:
        raise ValueError(f"unknown shape '{sp.shape}' "
                         "(use rect | rrect | octagon | circle)")

    # ---- outer terminal: run outwards, away from the coil centre -----------
    sx, sy = coil[0]
    y_pad = -sp.width / 2.0 - sp.lead_out
    outer_lead = [(sx, y_pad), (sx, sy)]
    pad_outer = (sx, y_pad)

    # ---- inner terminal: short stub into the open window, then a via -------
    ex, ey = coil[-1]
    stub = max(sp.via_pad / 2.0 + 0.15, sp.trace)
    via = (ex, ey + stub)
    inner_stub = [(ex, ey), via]

    # ---- bottom-layer return bridge ---------------------------------------
    # Drop straight down from the via, crossing the turns at right angles
    # (shortest crossing, least coupling), then run out to the second pad.
    # If that drop line would sit underneath the outer lead, jog it clear.
    # put the return pad on whichever side keeps it on the board
    side = str(sp.pad_side).strip().lower()
    if side not in ("left", "right"):
        side = "right" if sx - sp.pad_pitch < -sp.length / 2.0 else "left"
    pad_return = (sx + (sp.pad_pitch if side == "right" else -sp.pad_pitch),
                  y_pad)
    x_drop = via[0]
    if abs(via[0] - sx) < sp.trace + sp.gap:
        x_drop = via[0] - 2.0 * (sp.trace + sp.gap)
    bridge: List[Pt] = []
    if sp.make_bridge:
        bridge = [via]
        if abs(x_drop - via[0]) > 1e-9:
            bridge.append((x_drop, via[1]))
        bridge += [(x_drop, y_pad), pad_return]

    return Geometry(coil=coil, outer_lead=outer_lead, inner_stub=inner_stub,
                    bridge=bridge, pad_outer=pad_outer,
                    pad_return=pad_return, via=via)


def path_length(pts: List[Pt]) -> float:
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


# ============================================================================ #
#  3.  ELECTRICAL MODEL                                                        #
# ============================================================================ #
def _Q(x: float, d: float) -> float:
    """Antiderivative of the parallel-filament mutual-inductance integral."""
    return x * math.asinh(x / d) - math.hypot(x, d)


def mutual_parallel(a1: float, b1: float, a2: float, b2: float, d: float) -> float:
    """
    Exact mutual inductance [H] of two parallel filaments lying on a common axis
    direction, spanning [a1,b1] and [a2,b2] along that axis, separated by the
    (geometric-mean) distance d.  Lengths in metres.
    """
    if d <= 0:
        return 0.0
    return 1e-7 * (_Q(b1 - a2, d) + _Q(a1 - b2, d)
                   - _Q(b1 - b2, d) - _Q(a1 - a2, d))


def self_inductance(length: float, w: float, t: float) -> float:
    """Partial self-inductance [H] of a straight w x t bar of given length [m]."""
    if length <= 0:
        return 0.0
    gmd = 0.2235 * (w + t)                 # Grover GMD of a rectangular section
    return mutual_parallel(0.0, length, 0.0, length, gmd)


def greenhouse_inductance(path: List[Pt], w_mm: float, t_mm: float,
                          parallel_tol: float = 1e-3) -> float:
    """
    Total low-frequency inductance [H] of a planar current path by partial-
    inductance summation.  Only parallel / antiparallel segment pairs contribute
    (orthogonal filaments have zero mutual inductance), which makes this exact
    for rectangular spirals and a very good approximation elsewhere.
    """
    w = w_mm * 1e-3
    t = t_mm * 1e-3
    segs = []
    for i in range(len(path) - 1):
        p, q = path[i], path[i + 1]
        vx, vy = (q[0] - p[0]) * 1e-3, (q[1] - p[1]) * 1e-3
        L = math.hypot(vx, vy)
        if L < 1e-9:
            continue
        segs.append((p[0] * 1e-3, p[1] * 1e-3, vx / L, vy / L, L))

    gmd = 0.2235 * (w + t)          # floor for (near-)collinear filaments
    total = sum(self_inductance(s[4], w, t) for s in segs)

    for i in range(len(segs)):
        x1, y1, ux1, uy1, l1 = segs[i]
        for j in range(i + 1, len(segs)):
            x2, y2, ux2, uy2, l2 = segs[j]
            dot = ux1 * ux2 + uy1 * uy2
            if abs(abs(dot) - 1.0) > parallel_tol:
                continue                               # not parallel -> M = 0
            sign = 1.0 if dot > 0 else -1.0
            # perpendicular offset between the two axes (GMD-floored)
            dx, dy = x2 - x1, y2 - y1
            d = max(abs(-uy1 * dx + ux1 * dy), gmd)
            # axial coordinates measured along segment i's direction
            a1, b1 = 0.0, l1
            s2 = dx * ux1 + dy * uy1
            a2, b2 = (s2, s2 + l2 * dot) if dot > 0 else (s2 + l2 * dot, s2)
            total += 2.0 * sign * mutual_parallel(a1, b1, a2, b2, d)
    return total


def mohan_inductance(sp: AntennaSpec) -> float:
    """Mohan et al. current-sheet expression -- independent sanity check [H]."""
    build_up = sp.turns * sp.trace + (sp.turns - 1) * sp.gap
    dox, doy = sp.length, sp.width
    dix, diy = dox - 2 * build_up, doy - 2 * build_up
    d_out = math.sqrt(dox * doy) * 1e-3
    d_in = math.sqrt(max(dix, 1e-6) * max(diy, 1e-6)) * 1e-3
    d_avg = 0.5 * (d_out + d_in)
    rho = (d_out - d_in) / (d_out + d_in)
    c = {"rect": (1.27, 2.07, 0.18, 0.13), "rrect": (1.27, 2.07, 0.18, 0.13),
         "octagon": (1.07, 2.29, 0.00, 0.19),
         "circle": (1.00, 2.46, 0.00, 0.20)}[sp.shape]
    n = sp.turns
    return (MU0 * n * n * d_avg * c[0] / 2.0) * (
        math.log(c[1] / rho) + c[2] * rho + c[3] * rho * rho)


def ideal_closed_turns(sp: AntennaSpec) -> float:
    """
    Inductance [H] of the idealised coil: N perfectly closed concentric
    rectangles, i.e. what ST's NFC tool and most textbook models assume.
    Slightly optimistic (it ignores the entry gap), but it is the number to
    compare against published calculators.  Orthogonal shapes only.
    """
    pts: List[Pt] = []
    for i in range(sp.turns):
        ax, ay = _half_extents(sp, i)
        pts += [(ax, -ay), (ax, ay), (-ax, ay), (-ax, -ay), (ax, -ay)]
    return greenhouse_inductance(pts, sp.trace, sp.thickness)


def electrical(sp: AntennaSpec, geo: Geometry) -> dict:
    path = geo.top_path if sp.include_leads else geo.coil
    # For rounded corners the arcs are polygonised into short non-orthogonal
    # facets, which the parallel-only mutual sum cannot see. Corner rounding
    # changes the enclosed area by well under 2%, so evaluate L on the
    # equivalent sharp-cornered centreline instead of losing that coupling.
    L_mohan = mohan_inductance(sp)
    orthogonal = sp.shape in ("rect", "rrect")

    # Greenhouse only sums parallel/antiparallel pairs, which is exact for
    # orthogonal spirals but drops a large positive term for round coils.
    model = sp.model
    if model == "auto":
        model = "greenhouse" if orthogonal else "mohan"

    if model == "greenhouse":
        l_path = _rect_spiral(sp) if sp.shape == "rrect" else path
        L_gh = greenhouse_inductance(l_path, sp.trace, sp.thickness)
    else:
        # O(n^2) over hundreds of facets, and not the number we report for
        # round coils - skip it so the GUI stays responsive.
        L_gh = float("nan")
    L_ideal = ideal_closed_turns(sp) if orthogonal else float("nan")
    L = L_gh if model == "greenhouse" else L_mohan

    # ---- resistance --------------------------------------------------------
    l_cu = (path_length(geo.top_path) + path_length(geo.bridge)) * 1e-3   # m
    w, t = sp.trace * 1e-3, sp.thickness * 1e-3
    r_dc = RHO_CU * l_cu / (w * t)
    f = sp.freq_mhz * 1e6
    delta = math.sqrt(RHO_CU / (math.pi * f * MU0))             # skin depth, m
    # smooth conducting shell: effective penetration saturates at half the
    # dimension, so the model degrades gracefully to R_dc at low frequency
    dt = delta * (1.0 - math.exp(-t / (2.0 * delta)))
    dw = delta * (1.0 - math.exp(-w / (2.0 * delta)))
    a_ac = w * t - max(w - 2 * dw, 0.0) * max(t - 2 * dt, 0.0)
    r_ac = max(RHO_CU * l_cu / max(a_ac, 1e-18), r_dc) * sp.proximity_factor

    omega = 2 * math.pi * f
    c_tune = 1.0 / (omega * omega * L) if L > 0 else float("nan")
    q = omega * L / r_ac if r_ac > 0 else float("nan")

    res = {
        "model_used": model,
        "L_uH": L * 1e6,
        "L_greenhouse_uH": L_gh * 1e6,
        "L_mohan_uH": L_mohan * 1e6,
        "L_ideal_closed_turns_uH": L_ideal * 1e6,
        "conductor_length_mm": l_cu * 1e3,
        "n_segments": len(path) - 1,
        "R_dc_ohm": r_dc,
        "R_ac_ohm": r_ac,
        "skin_depth_um": delta * 1e6,
        "Q": q,
        "f_MHz": sp.freq_mhz,
        "C_total_resonant_pF": c_tune * 1e12,
    }
    if sp.chip_cap_pf > 0:
        c_ext = c_tune * 1e12 - sp.chip_cap_pf
        res["C_chip_pF"] = sp.chip_cap_pf
        res["C_external_pF"] = c_ext
        if c_ext <= 0:
            res["warning_tuning"] = ("chip capacitance alone already exceeds the "
                                     "resonating value - reduce L (fewer turns / "
                                     "smaller loop)")
    return res


# ============================================================================ #
#  4.  COPPER OUTLINE                                                          #
# ============================================================================ #
def measure_clearance(pts: List[Pt], trace: float, gap: float,
                      _iters: int = 0) -> float:
    """
    Exact minimum copper-to-copper clearance of the drawn path.

    Computes the smallest distance between any two segments that are far apart
    *along the conductor* but close in space -- i.e. between different turns,
    not between neighbouring segments of the same turn, which are always close
    near their shared corner. An STRtree keeps it near-linear.

    This is a real DRC number, not the nominal parameter: faceted shapes lose a
    little of the gap to the polygonal approximation, and a partially-rounded
    spiral loses a lot of it where a sharp corner sits inside a rounded one.
    """
    if not HAVE_SHAPELY or len(pts) < 3:
        return float("nan")
    from shapely.strtree import STRtree

    segs, arc = [], [0.0]
    for i in range(len(pts) - 1):
        if math.dist(pts[i], pts[i + 1]) < 1e-12:
            continue
        segs.append(LineString([pts[i], pts[i + 1]]))
        arc.append(arc[-1] + segs[-1].length)
    if len(segs) < 3:
        return float("nan")

    # two segments belong to the same conductor run if little path length
    # separates them; only compare across that horizon
    horizon = 3.0 * (trace + gap)
    tree = STRtree(segs)
    best = float("inf")
    for i, seg in enumerate(segs):
        for j in tree.query(seg.buffer(2.0 * (trace + gap))):
            j = int(j)
            if j <= i:
                continue
            # arc[i+1] is the far end of segment i: measure the path
            # distance from there to the near end of segment j
            if arc[j] - arc[i + 1] < horizon:
                continue
            d = seg.distance(segs[j])
            if d < best:
                best = d
    return max(0.0, best - trace) if best < float("inf") else float("nan")


def copper_polygons(paths: List[Tuple[List[Pt], float]]):
    """Buffer centrelines into a copper region. Returns [(exterior, [holes]), ...]."""
    if not HAVE_SHAPELY:
        return []
    geoms = []
    for pts, w in paths:
        if len(pts) < 2 or w <= 0:
            continue
        geoms.append(LineString(pts).buffer(w / 2.0, cap_style=2, join_style=2,
                                            mitre_limit=4.0, resolution=16))
    if not geoms:
        return []
    merged = unary_union(geoms)
    polys = merged.geoms if isinstance(merged, MultiPolygon) else [merged]
    out = []
    for p in polys:
        if isinstance(p, Polygon) and not p.is_empty:
            out.append((list(p.exterior.coords),
                        [list(r.coords) for r in p.interiors]))
    return out


# ============================================================================ #
#  5.  DXF EXPORT                                                              #
# ============================================================================ #
LAYERS = {
    "ANT_CENTERLINE":   (5,   "Trace centreline, const width = conductor width"),
    "ANT_COPPER":       (3,   "Top-layer copper outline (closed polygons)"),
    "ANT_COPPER_HATCH": (3,   "Top-layer copper solid fill"),
    "ANT_BOTTOM":       (1,   "Bottom-layer return bridge centreline"),
    "ANT_PADS":         (2,   "Terminal pads"),
    "ANT_VIA":          (6,   "Via land / drill"),
    "ANT_KEEPOUT":      (8,   "Coil bounding box"),
    "BOARD_OUTLINE":    (7,   "Board edge"),
    "ANT_INFO":         (4,   "Annotation"),
}


def write_dxf(sp: AntennaSpec, geo: Geometry, res: dict, path: str) -> str:
    if not HAVE_EZDXF:
        raise RuntimeError(
            "ezdxf is not installed in the Python that is running this script."
            + dependency_report())

    doc = ezdxf.new("R2010", setup=True)
    doc.units = dxf_units.MM
    doc.header["$INSUNITS"] = 4          # millimetres
    doc.header["$MEASUREMENT"] = 1       # metric
    for name, (color, _desc) in LAYERS.items():
        doc.layers.add(name, color=color)
    msp = doc.modelspace()

    # --- 1. centreline with constant width (KiCad/Altium read this as a trace)
    top = geo.top_path
    pl = msp.add_lwpolyline([(x, y) for x, y in top],
                            dxfattribs={"layer": "ANT_CENTERLINE",
                                        "const_width": sp.trace})
    pl.close(False)

    if geo.bridge:
        b = msp.add_lwpolyline(geo.bridge,
                               dxfattribs={"layer": "ANT_BOTTOM",
                                           "const_width": sp.trace})
        b.close(False)

    # --- 2. copper outline (true drawn shape of the etched copper) ----------
    paths = [(top, sp.trace)]
    polys = copper_polygons(paths)
    for ext, holes in polys:
        msp.add_lwpolyline(ext, close=True, dxfattribs={"layer": "ANT_COPPER"})
        for h in holes:
            msp.add_lwpolyline(h, close=True, dxfattribs={"layer": "ANT_COPPER"})
        if sp.hatch_copper:
            hatch = msp.add_hatch(color=3, dxfattribs={"layer": "ANT_COPPER_HATCH"})
            hatch.paths.add_polyline_path(ext, is_closed=True,
                                          flags=ezdxf.const.BOUNDARY_PATH_EXTERNAL)
            for h in holes:
                hatch.paths.add_polyline_path(h, is_closed=True,
                                              flags=ezdxf.const.BOUNDARY_PATH_OUTERMOST)

    # --- 3. pads, via ------------------------------------------------------
    def rect_pad(cx, cy, w, h, layer):
        msp.add_lwpolyline([(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
                            (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)],
                           close=True, dxfattribs={"layer": layer})

    rect_pad(*geo.pad_outer, sp.pad_w, sp.pad_h, "ANT_PADS")
    if sp.make_bridge:
        rect_pad(*geo.pad_return, sp.pad_w, sp.pad_h, "ANT_PADS")
    msp.add_circle(geo.via, sp.via_pad / 2.0, dxfattribs={"layer": "ANT_VIA"})
    msp.add_circle(geo.via, sp.via_drill / 2.0, dxfattribs={"layer": "ANT_VIA"})

    # --- 4. keepout / board -------------------------------------------------
    hx, hy = sp.length / 2.0, sp.width / 2.0
    msp.add_lwpolyline([(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)],
                       close=True, dxfattribs={"layer": "ANT_KEEPOUT"})
    if sp.board_margin >= 0:
        m = sp.board_margin
        y0 = -hy - sp.lead_out - sp.pad_h / 2.0 - m
        msp.add_lwpolyline([(-hx - m, y0), (hx + m, y0),
                            (hx + m, hy + m), (-hx - m, hy + m)],
                           close=True, dxfattribs={"layer": "BOARD_OUTLINE"})

    # --- 5. annotation ------------------------------------------------------
    lines = [
        f"NFC SPIRAL ANTENNA  ({sp.shape})",
        f"outer {sp.length:g} x {sp.width:g} mm   turns {sp.turns}",
        f"trace {sp.trace:g} mm   gap {sp.gap:g} mm   Cu {sp.thickness_um:g} um",
        f"L = {res['L_uH']:.3f} uH  ({res['model_used']}; "
        f"Mohan check {res['L_mohan_uH']:.3f} uH)",
        f"Rdc {res['R_dc_ohm']:.3f} ohm   Rac {res['R_ac_ohm']:.3f} ohm   "
        f"Q {res['Q']:.1f} @ {sp.freq_mhz:g} MHz",
        f"C for resonance = {res['C_total_resonant_pF']:.1f} pF   "
        f"conductor {res['conductor_length_mm']:.1f} mm",
        "units: MILLIMETRES",
    ]
    y = hy + 2.0
    for i, txt in enumerate(lines):
        msp.add_text(txt, height=0.9,
                     dxfattribs={"layer": "ANT_INFO"}
                     ).set_placement((-hx, y + 1.4 * (len(lines) - i)))

    doc.saveas(path)
    return path


# ============================================================================ #
#  6.  KiCad FOOTPRINT EXPORT (bonus: copper traces, no conversion needed)      #
# ============================================================================ #
def write_kicad_mod(sp: AntennaSpec, geo: Geometry, name: str, path: str) -> str:
    def seg(p, q, layer, w):
        return (f'  (fp_line (start {p[0]:.4f} {-p[1]:.4f}) '
                f'(end {q[0]:.4f} {-q[1]:.4f}) '
                f'(stroke (width {w:.4f}) (type solid)) (layer "{layer}"))')

    out = [f'(footprint "{name}" (version 20221018) (generator nfc_spiral)',
           '  (layer "F.Cu")',
           '  (attr smd)',
           f'  (fp_text reference "ANT1" (at 0 {-(sp.width/2+4):.3f}) '
           '(layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))',
           f'  (fp_text value "{name}" (at 0 {(sp.width/2+4):.3f}) '
           '(layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))']

    top = geo.top_path
    for i in range(len(top) - 1):
        out.append(seg(top[i], top[i + 1], "F.Cu", sp.trace))
    for i in range(len(geo.bridge) - 1):
        out.append(seg(geo.bridge[i], geo.bridge[i + 1], "B.Cu", sp.trace))

    px, py = geo.pad_outer
    out.append(f'  (pad "1" smd rect (at {px:.4f} {-py:.4f}) '
               f'(size {sp.pad_w:.3f} {sp.pad_h:.3f}) '
               '(layers "F.Cu" "F.Paste" "F.Mask"))')
    if sp.make_bridge:
        rx, ry = geo.pad_return
        out.append(f'  (pad "2" smd rect (at {rx:.4f} {-ry:.4f}) '
                   f'(size {sp.pad_w:.3f} {sp.pad_h:.3f}) '
                   '(layers "B.Cu" "B.Paste" "B.Mask"))')
    vx, vy = geo.via
    out.append(f'  (pad "2" thru_hole circle (at {vx:.4f} {-vy:.4f}) '
               f'(size {sp.via_pad:.3f} {sp.via_pad:.3f}) '
               f'(drill {sp.via_drill:.3f}) (layers "*.Cu"))')
    out.append(")")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return path


# ============================================================================ #
#  7.  PREVIEW                                                                 #
# ============================================================================ #
def write_preview(sp: AntennaSpec, geo: Geometry, res: dict, path: str) -> Optional[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon as MplPoly, Circle
    except ImportError:                                # pragma: no cover
        return None

    fig, ax = plt.subplots(figsize=(9, 9 * max(0.35, sp.width / max(sp.length, 1e-9))
                                    + 1.6))
    polys = copper_polygons([(geo.top_path, sp.trace)])
    if polys:
        for ext, holes in polys:
            ax.add_patch(MplPoly(ext, closed=True, facecolor="#b87333",
                                 edgecolor="#7a4a1f", lw=0.4, zorder=2))
            for h in holes:
                ax.add_patch(MplPoly(h, closed=True, facecolor="#12452b",
                                     edgecolor="#7a4a1f", lw=0.4, zorder=3))
    else:
        xs, ys = zip(*geo.top_path)
        ax.plot(xs, ys, color="#b87333", lw=2, zorder=2)
    if geo.bridge:
        xs, ys = zip(*geo.bridge)
        ax.plot(xs, ys, color="#4da3ff", lw=max(1.4, sp.trace * 2.5), alpha=0.9,
                ls=(0, (5, 2)), solid_capstyle="butt", zorder=5,
                label="bottom layer")
    ax.add_patch(Circle(geo.via, sp.via_pad / 2, facecolor="#d0d0d0",
                        edgecolor="k", lw=0.4, zorder=4))
    for c in ([geo.pad_outer, geo.pad_return] if sp.make_bridge else [geo.pad_outer]):
        ax.add_patch(MplPoly([(c[0] - sp.pad_w / 2, c[1] - sp.pad_h / 2),
                              (c[0] + sp.pad_w / 2, c[1] - sp.pad_h / 2),
                              (c[0] + sp.pad_w / 2, c[1] + sp.pad_h / 2),
                              (c[0] - sp.pad_w / 2, c[1] + sp.pad_h / 2)],
                             closed=True, facecolor="#e8c33a", edgecolor="k",
                             lw=0.4, zorder=4))
    ax.set_facecolor("#0f3a25")
    ax.set_aspect("equal")
    m = 3
    ax.set_xlim(-sp.length / 2 - m, sp.length / 2 + m)
    ax.set_ylim(-sp.width / 2 - sp.lead_out - sp.pad_h - m, sp.width / 2 + m)
    ax.set_xlabel("x [mm]")
    ax.set_ylabel("y [mm]")
    ax.set_title(
        f"{sp.shape}  {sp.length:g}x{sp.width:g} mm  N={sp.turns}  "
        f"w={sp.trace:g}  s={sp.gap:g} mm\n"
        f"L = {res['L_uH']:.3f} uH   "
        f"Rac = {res['R_ac_ohm']:.2f} ohm   Q = {res['Q']:.0f}   "
        f"C_res = {res['C_total_resonant_pF']:.1f} pF @ {sp.freq_mhz:g} MHz",
        fontsize=10)
    ax.grid(alpha=0.15, color="w")
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path


# ============================================================================ #
#  8.  TOP-LEVEL DRIVER                                                        #
# ============================================================================ #
def build(sp: AntennaSpec, outdir: str = ".", name: str = "nfc_antenna",
          dxf: bool = True, kicad: bool = True, preview: bool = True,
          strict: bool = True, verbose: bool = True) -> dict:
    errs = sp.validate()
    hard = [e for e in errs if e.startswith("error:")]
    if errs and verbose:
        for e in errs:
            tag = "ERROR" if e.startswith("error:") else "warn "
            print(f"  [{tag}] {e.split(': ', 1)[-1]}", file=sys.stderr)
    if hard and strict:
        raise ValueError("geometry is not realisable: " + "; ".join(hard))

    os.makedirs(outdir, exist_ok=True)
    geo = build_geometry(sp)
    res = electrical(sp, geo)
    res["min_clearance_mm"] = measure_clearance(geo.top_path, sp.trace, sp.gap)
    if (not math.isnan(res["min_clearance_mm"])
            and res["min_clearance_mm"] < 0.98 * sp.gap):
        msg = (f"drawn clearance {res['min_clearance_mm']:.4f} mm is below the "
               f"requested gap {sp.gap:g} mm (polygon approximation of the "
               f"curve) - raise --gap or check against your fab's minimum")
        errs.append("warn: " + msg)
        if verbose:
            print(f"  [warn ] {msg}", file=sys.stderr)

    files = {}
    if dxf:
        if HAVE_EZDXF:
            files["dxf"] = write_dxf(sp, geo, res,
                                     os.path.join(outdir, name + ".dxf"))
        else:
            # Don't lose the whole run over one missing package: the KiCad
            # footprint and the JSON need nothing but the standard library.
            print("  [warn ] skipping DXF export - ezdxf is not installed."
                  + dependency_report(), file=sys.stderr)
    if kicad:
        files["kicad_mod"] = write_kicad_mod(
            sp, geo, name, os.path.join(outdir, name + ".kicad_mod"))
    if preview:
        p = write_preview(sp, geo, res, os.path.join(outdir, name + ".png"))
        if p:
            files["png"] = p

    payload = {"parameters": asdict(sp), "results": res,
               "warnings": errs, "files": files}
    files["json"] = os.path.join(outdir, name + ".json")
    with open(files["json"], "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    # always hand back absolute paths so the files are findable
    files = {k: os.path.abspath(v) for k, v in files.items()}
    payload["files"] = files

    if verbose:
        print(report(sp, res, files))
    return payload


def solve_turns(sp: AntennaSpec, target_uH: float, n_max: int = 60,
                verbose: bool = True) -> Optional[int]:
    """
    Sweep the turn count at fixed outline / trace / gap and report the N whose
    inductance is closest to the target.  Returns that N (None if unreachable).
    """
    rows = []
    for n in range(1, n_max + 1):
        trial = AntennaSpec(**{**asdict(sp), "turns": n})
        if [e for e in trial.validate() if e.startswith("error:")]:
            break
        geo = build_geometry(trial)
        r = electrical(trial, geo)
        rows.append((n, r["L_uH"], r["R_ac_ohm"], r["Q"],
                     r["C_total_resonant_pF"]))
    if not rows:
        return None
    best = min(rows, key=lambda r: abs(r[1] - target_uH))
    if verbose:
        print(f"\n  turn sweep for {sp.length:g} x {sp.width:g} mm, "
              f"w={sp.trace:g} mm, s={sp.gap:g} mm   "
              f"(target {target_uH:g} uH)")
        print("   N     L[uH]   R_ac[ohm]     Q    C_res[pF]")
        for n, L, R, Q, C in rows:
            mark = "  <== best" if n == best[0] else ""
            print(f"  {n:2d}   {L:7.3f}   {R:8.3f}  {Q:6.1f}   {C:8.2f}{mark}")
    return best[0]


def report(sp: AntennaSpec, res: dict, files: dict) -> str:
    w = 66
    L = [
        "=" * w,
        f" NFC SPIRAL ANTENNA  --  {sp.shape}",
        "=" * w,
        f"  outer size ............ {sp.length:g} x {sp.width:g} mm",
        f"  turns ................. {sp.turns}",
        f"  conductor w / gap ..... {sp.trace:g} / {sp.gap:g} mm  "
        f"(pitch {sp.pitch:g} mm)",
        f"  copper thickness ...... {sp.thickness_um:g} um",
        f"  inner window .......... "
        f"{sp.length - 2*(sp.turns*sp.trace + (sp.turns-1)*sp.gap):.2f} x "
        f"{sp.width - 2*(sp.turns*sp.trace + (sp.turns-1)*sp.gap):.2f} mm",
        f"  conductor length ...... {res['conductor_length_mm']:.1f} mm "
        f"({res['n_segments']} segments modelled)",
        f"  measured clearance .... {res.get('min_clearance_mm', float('nan')):.4f}"
        f" mm  (requested {sp.gap:g} mm)",
        f"  terminal .............. x = {sp.term_x:+.2f} mm on the bottom edge "
        f"('{sp.term_pos}'; range {sp.term_x_min:+.2f} to {sp.term_x_max:+.2f})",]
    L += [
        "-" * w,
        f"  INDUCTANCE ............ {res['L_uH']:.4f} uH"
        f"   <-- {res['model_used']} model",
    ]
    if not math.isnan(res["L_ideal_closed_turns_uH"]):
        L.append(f"    ideal closed turns .. {res['L_ideal_closed_turns_uH']:.4f} uH"
                 "  (comparable to ST / textbook calculators)")
    L += [
        f"    Mohan current sheet  {res['L_mohan_uH']:.4f} uH  (independent check)",]
    if not math.isnan(res["L_greenhouse_uH"]) and res["model_used"] != "greenhouse":
        L.append(f"    Greenhouse .......... {res['L_greenhouse_uH']:.4f} uH")
    L += [
        f"  R_dc / R_ac ........... {res['R_dc_ohm']:.4f} / "
        f"{res['R_ac_ohm']:.4f} ohm   (skin depth "
        f"{res['skin_depth_um']:.1f} um)",
        f"  Q @ {sp.freq_mhz:g} MHz ......... {res['Q']:.1f}",
        f"  C for resonance ....... {res['C_total_resonant_pF']:.2f} pF total",
    ]
    if "C_external_pF" in res:
        L.append(f"  - chip {res['C_chip_pF']:g} pF  ->  external tuning cap "
                 f"{res['C_external_pF']:.2f} pF")
    if "warning_tuning" in res:
        L.append(f"  !! {res['warning_tuning']}")
    L.append("-" * w)
    L.append("  FILES WRITTEN:")
    for k, v in files.items():
        L.append(f"    {k:<10} {v}")
    if files:
        L.append(f"  folder: {os.path.dirname(next(iter(files.values())))}")
    L.append("=" * w)
    return "\n".join(L)


# ============================================================================ #
#  9.  CLI                                                                     #
# ============================================================================ #
def _defaults() -> dict:
    """
    The AntennaSpec field defaults, used as the argparse defaults too.

    This is what makes editing the dataclass at the top of this file actually
    work: change `corner_r` there, run the script with no arguments, and you
    get that value. Anything you pass on the command line still wins.
    """
    import dataclasses
    return {f.name: f.default for f in dataclasses.fields(AntennaSpec)}


def main(argv=None):
    D = _defaults()
    p = argparse.ArgumentParser(
        description="Parametric NFC/RFID planar spiral antenna -> DXF for ECAD. "
                    "Defaults come from the AntennaSpec dataclass at the top of "
                    "this file, so you can edit them there instead of typing "
                    "arguments; command-line values override them.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    g = p.add_argument_group("geometry")
    g.add_argument("--shape", default=D["shape"],
                   choices=["rect", "rrect", "octagon", "circle"])
    g.add_argument("--length", type=float, default=D["length"], help="outer X size [mm]")
    g.add_argument("--width", type=float, default=D["width"], help="outer Y size [mm]")
    g.add_argument("--turns", type=int, default=D["turns"])
    g.add_argument("--trace", type=float, default=D["trace"], help="conductor width [mm]")
    g.add_argument("--gap", type=float, default=D["gap"], help="turn-to-turn gap [mm]")
    g.add_argument("--corner-r", default=str(D["corner_r"]),
                   help="outer-turn corner radius [mm], or 'auto'. Inner turns "
                        "shrink by one pitch each so all corner arcs stay "
                        "concentric and the gap is constant round the corner. "
                        "Any value > 0 selects shape=rrect automatically.")
    g.add_argument("--cu-um", type=float, default=D["thickness_um"], dest="thickness_um",
                   help="copper thickness [um]")

    t = p.add_argument_group("terminals")
    t.add_argument("--term-pos", default=str(D["term_pos"]),
                   help="where the terminal leaves the bottom edge: "
                        "right | center | left, or an x coordinate in mm. "
                        "The spiral's break staircase moves with it.")
    t.add_argument("--pad-side", default=D["pad_side"],
                   choices=["auto", "left", "right"],
                   help="which side of the outer pad the return pad sits")
    t.add_argument("--lead-out", type=float, default=D["lead_out"])
    t.add_argument("--pad-w", type=float, default=D["pad_w"])
    t.add_argument("--pad-h", type=float, default=D["pad_h"])
    t.add_argument("--pad-pitch", type=float, default=D["pad_pitch"])
    t.add_argument("--via-pad", type=float, default=D["via_pad"])
    t.add_argument("--via-drill", type=float, default=D["via_drill"])
    t.add_argument("--no-bridge", action="store_true",
                   help="do not draw the bottom-layer return trace")

    e = p.add_argument_group("electrical")
    e.add_argument("--freq", type=float, default=D["freq_mhz"], dest="freq_mhz",
                   help="carrier frequency [MHz]")
    e.add_argument("--chip-cap", type=float, default=D["chip_cap_pf"], dest="chip_cap_pf",
                   help="IC input capacitance [pF]")
    e.add_argument("--include-leads", action="store_true",
                   help="include the terminal leads in the inductance model")
    e.add_argument("--model", default=D["model"],
                   choices=["auto", "greenhouse", "mohan"],
                   help="inductance model (auto: greenhouse for orthogonal, "
                        "mohan for round/octagonal)")
    e.add_argument("--proximity", type=float, default=D["proximity_factor"],
                   dest="proximity_factor",
                   help="empirical proximity-effect multiplier on R_ac")
    e.add_argument("--target-l", type=float, default=None,
                   help="sweep the turn count to hit this inductance [uH] "
                        "and build with the best N")

    o = p.add_argument_group("output")
    o.add_argument("--out", default="nfc_antenna", help="output base name")
    o.add_argument("--outdir", default=None,
                   help="output folder (default: an 'output' folder next to "
                        "this script, so results never get lost in whatever "
                        "the current working directory happens to be)")
    o.add_argument("--board-margin", type=float, default=D["board_margin"],
                   help=">=0 draws a board outline at this margin [mm]")
    o.add_argument("--no-hatch", action="store_true")
    o.add_argument("--no-kicad", action="store_true")
    o.add_argument("--no-preview", action="store_true")
    o.add_argument("--force", action="store_true",
                   help="build even if the geometry checks fail")

    d = p.add_argument_group("setup")
    d.add_argument("--check-deps", action="store_true",
                   help="report which Python is running and what is installed")
    d.add_argument("--install-deps", action="store_true",
                   help="pip-install the missing packages into this interpreter")

    a = p.parse_args(argv)
    if a.outdir is None:
        a.outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "output")

    if a.install_deps:
        return install_deps()
    if a.check_deps:
        print(dependency_report())
        return 0
    # --corner-r accepts a number or 'auto'; any radius implies shape=rrect
    auto_r = str(a.corner_r).strip().lower() == "auto"
    corner_r = 0.0 if auto_r else float(a.corner_r)
    shape = a.shape
    if (auto_r or corner_r > 0) and shape == "rect":
        shape = "rrect"
    if auto_r:
        probe = AntennaSpec(shape=shape, length=a.length, width=a.width,
                            turns=a.turns, trace=a.trace, gap=a.gap,
                            term_pos=a.term_pos)
        # midway between "every turn rounded" and the largest radius that fits
        corner_r = round(0.5 * (probe.min_corner_r_all_rounded
                                + probe.max_corner_r), 3)
        print(f"  corner-r auto -> {corner_r:g} mm "
              f"(all turns rounded from {probe.min_corner_r_all_rounded:.2f} mm, "
              f"max that fits {probe.max_corner_r:.2f} mm)")

    sp = AntennaSpec(
        shape=shape, length=a.length, width=a.width, turns=a.turns,
        trace=a.trace, gap=a.gap, corner_r=corner_r,
        term_pos=a.term_pos, pad_side=a.pad_side,
        thickness_um=a.thickness_um, lead_out=a.lead_out, pad_w=a.pad_w,
        pad_h=a.pad_h, pad_pitch=a.pad_pitch, via_pad=a.via_pad,
        via_drill=a.via_drill, make_bridge=D["make_bridge"] and not a.no_bridge,
        board_margin=a.board_margin, hatch_copper=D["hatch_copper"] and not a.no_hatch,
        freq_mhz=a.freq_mhz, chip_cap_pf=a.chip_cap_pf,
        include_leads=D["include_leads"] or a.include_leads, model=a.model,
        proximity_factor=a.proximity_factor,
        arc_segments=D["arc_segments"], circle_segments=D["circle_segments"])

    if a.target_l is not None:
        n = solve_turns(sp, a.target_l)
        if n is None:
            print("no realisable turn count for this outline / trace / gap",
                  file=sys.stderr)
            return 1
        sp.turns = n
        print(f"\n  -> building with turns = {n}\n")

    build(sp, outdir=a.outdir, name=a.out, kicad=not a.no_kicad,
          preview=not a.no_preview, strict=not a.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
