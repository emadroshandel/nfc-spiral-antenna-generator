#!/usr/bin/env python3
"""
Test suite for nfc_spiral.py.

Two kinds of test:

  * physics   -- the inductance model is checked against closed-form results
                 that exist independently of this code (Grover's square-loop
                 formula, N^2 scaling, the Mohan current-sheet expression, and
                 ST's published 2.9 uH figure for the reference geometry).
  * geometry  -- every generated coil must be one connected piece of copper
                 with no enclosed islands and a measured clearance that meets
                 the requested gap. This is the check that catches a spiral
                 that has accidentally shorted its own turns together.

Run:  pytest -q tests/            (or just: python tests/test_nfc_spiral.py)
"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from nfc_spiral import (                                        # noqa: E402
    MU0, AntennaSpec, build, build_geometry, copper_polygons, electrical,
    greenhouse_inductance, ideal_closed_turns, measure_clearance,
    mohan_inductance, path_length, solve_turns,
)

ST = dict(length=46.0, width=20.0, turns=6, trace=0.24, gap=0.15)


# --------------------------------------------------------------------- physics
def test_closed_square_loop_matches_grover():
    """A single closed square loop against Grover's closed form.

    Grover's constant carries the DC internal inductance of a round wire, which
    a flat bar modelled by its GMD does not have, so a few percent of spread is
    expected and correct.
    """
    a_mm, w, t = 19.76, 0.24, 0.035
    sq = [(-a_mm / 2, -a_mm / 2), (a_mm / 2, -a_mm / 2), (a_mm / 2, a_mm / 2),
          (-a_mm / 2, a_mm / 2), (-a_mm / 2, -a_mm / 2)]
    L = greenhouse_inductance(sq, w, t)

    a = a_mm * 1e-3
    r = 0.2235 * (w + t) * 1e-3 / math.exp(-0.25)
    L_ref = (2 * MU0 * a / math.pi) * (math.log(a / r) - 0.774)
    assert abs(L / L_ref - 1.0) < 0.10


def test_st_reference_inductance():
    """The idealised model should land near ST's published 2.9 uH."""
    sp = AntennaSpec(**ST)
    assert abs(ideal_closed_turns(sp) * 1e6 - 2.9) / 2.9 < 0.06


def test_models_agree_within_ten_percent():
    sp = AntennaSpec(**ST)
    gh = ideal_closed_turns(sp) * 1e6
    mo = mohan_inductance(sp) * 1e6
    assert abs(gh - mo) / gh < 0.10


def test_inductance_scales_faster_than_linearly_in_turns():
    """Mutual coupling means L grows super-linearly, approaching N^2."""
    L = []
    for n in (2, 4, 8):
        sp = AntennaSpec(**{**ST, "turns": n})
        L.append(electrical(sp, build_geometry(sp))["L_uH"])
    assert L[1] / L[0] > 2.5
    assert L[2] / L[1] > 2.5


def test_resonant_capacitance_round_trips():
    sp = AntennaSpec(**ST, chip_cap_pf=28.5)
    r = electrical(sp, build_geometry(sp))
    omega = 2 * math.pi * sp.freq_mhz * 1e6
    f_calc = 1.0 / (2 * math.pi * math.sqrt(
        (r["L_uH"] * 1e-6) * (r["C_total_resonant_pF"] * 1e-12)))
    assert abs(f_calc / 1e6 - sp.freq_mhz) < 1e-6
    assert r["C_external_pF"] == pytest.approx(
        r["C_total_resonant_pF"] - 28.5, rel=1e-9)
    assert omega > 0


def test_ac_resistance_never_below_dc():
    for f in (0.1, 1.0, 13.56, 100.0):
        sp = AntennaSpec(**ST, freq_mhz=f)
        r = electrical(sp, build_geometry(sp))
        assert r["R_ac_ohm"] >= r["R_dc_ohm"] - 1e-12


def test_solve_turns_hits_the_target():
    sp = AntennaSpec(**ST)
    n = solve_turns(sp, 2.0, verbose=False)
    assert n is not None
    got = electrical(AntennaSpec(**{**ST, "turns": n}),
                     build_geometry(AntennaSpec(**{**ST, "turns": n})))["L_uH"]
    for other in (n - 1, n + 1):
        if other < 1:
            continue
        sp2 = AntennaSpec(**{**ST, "turns": other})
        if [e for e in sp2.validate() if e.startswith("error:")]:
            continue
        alt = electrical(sp2, build_geometry(sp2))["L_uH"]
        assert abs(got - 2.0) <= abs(alt - 2.0)


# -------------------------------------------------------------------- geometry
SHAPES = [
    dict(shape="rect"),
    dict(shape="rrect", corner_r=5.9),
    dict(shape="octagon", length=30.0, width=30.0),
    dict(shape="circle", length=30.0, width=30.0),
]
TERMS = ["right", "center", "left", -6.0, 6.0]


@pytest.mark.parametrize("extra", SHAPES)
@pytest.mark.parametrize("term", TERMS)
def test_copper_is_one_connected_piece(extra, term):
    """The single most important invariant: the coil must not short itself.

    If the spiral's step-in segments ever line up, adjacent turns fuse and the
    buffered copper stops being a simple polygon (or grows an enclosed hole).
    """
    sp = AntennaSpec(**{**ST, **extra, "term_pos": term})
    if [e for e in sp.validate() if e.startswith("error:")]:
        pytest.skip("geometry not realisable for this combination")
    geo = build_geometry(sp)
    polys = copper_polygons([(geo.top_path, sp.trace)])
    assert len(polys) == 1, "copper broke into disconnected pieces"
    assert len(polys[0][1]) == 0, "copper enclosed an island (turns fused)"


@pytest.mark.parametrize("extra", SHAPES)
@pytest.mark.parametrize("term", TERMS)
def test_clearance_meets_the_requested_gap(extra, term):
    sp = AntennaSpec(**{**ST, **extra, "term_pos": term})
    if [e for e in sp.validate() if e.startswith("error:")]:
        pytest.skip("geometry not realisable for this combination")
    geo = build_geometry(sp)
    c = measure_clearance(geo.top_path, sp.trace, sp.gap)
    # faceted shapes lose a few microns to the polygonal approximation
    assert c >= 0.98 * sp.gap, f"clearance {c:.4f} mm vs gap {sp.gap} mm"


def test_corner_arcs_are_concentric():
    """Every turn's corner arc must share one centre, radii stepping by pitch."""
    sp = AntennaSpec(**ST, shape="rrect", corner_r=5.915)
    geo = build_geometry(sp)
    ax = sp.length / 2 - sp.trace / 2
    ay = sp.width / 2 - sp.trace / 2
    centre = (ax - sp.corner_r, ay - sp.corner_r)
    for i in range(sp.turns):
        r_i = sp.corner_r - i * sp.pitch
        on_arc = [p for p in geo.coil
                  if p[0] > centre[0] and p[1] > centre[1]
                  and abs(math.dist(p, centre) - r_i) < 1e-7]
        assert len(on_arc) > 3, f"turn {i} has no top-right arc"


def test_partial_rounding_is_flagged():
    """A radius too small to survive to the inner turns must warn, not pass."""
    sp = AntennaSpec(**ST, shape="rrect", corner_r=1.0)
    warns = [e for e in sp.validate() if e.startswith("warn:")]
    assert any("sharp corners" in w for w in warns)


def test_rounding_never_reduces_clearance():
    """A fillet cuts a corner away, so it can only move copper further apart.

    Guards the concentric-radius scheme: if radii ever stopped shrinking by
    exactly one pitch, the corner gap would change and this would catch it.
    """
    base = AntennaSpec(**ST)
    ref = measure_clearance(build_geometry(base).top_path, base.trace, base.gap)
    for cr in (0.5, 1.0, 2.0, 5.9, 9.0):
        sp = AntennaSpec(**ST, shape="rrect", corner_r=cr)
        c = measure_clearance(build_geometry(sp).top_path, sp.trace, sp.gap)
        assert c >= 0.98 * ref, f"corner_r={cr} cut clearance to {c:.4f} mm"


def test_elongated_round_shapes_are_rejected():
    """Stretching a circle or octagon pinches the gap - must be a hard error."""
    for shape in ("circle", "octagon"):
        sp = AntennaSpec(**ST, shape=shape)          # 46 x 20 = aspect 2.3
        assert [e for e in sp.validate() if e.startswith("error:")]


def test_polygon_chord_compensation():
    """Octagon radial pitch must exceed the nominal pitch by 1/cos(pi/8)."""
    sp = AntennaSpec(length=30.0, width=30.0, turns=6, trace=0.24, gap=0.15,
                     shape="octagon")
    assert sp.radial_pitch == pytest.approx(sp.pitch / math.cos(math.pi / 8))
    assert AntennaSpec(**ST).radial_pitch == pytest.approx(AntennaSpec(**ST).pitch)


def test_terminal_position_is_clamped_and_flagged():
    sp = AntennaSpec(**ST, term_pos=999.0)
    assert sp.term_x == pytest.approx(sp.term_x_max)
    assert any("outside the feasible range" in e for e in sp.validate())


def test_impossible_turn_count_is_a_hard_error():
    sp = AntennaSpec(**{**ST, "turns": 200})
    assert [e for e in sp.validate() if e.startswith("error:")]


def test_conductor_length_is_sane():
    """Roughly N turns around the perimeter, within a generous band."""
    sp = AntennaSpec(**ST)
    geo = build_geometry(sp)
    perimeter = 2 * (sp.length + sp.width)
    got = path_length(geo.coil)
    assert 0.8 * sp.turns * perimeter < got < 1.05 * sp.turns * perimeter


# ----------------------------------------------------------------- integration
def test_full_build_writes_every_file(tmp_path):
    sp = AntennaSpec(**ST, shape="rrect", corner_r=5.9, term_pos="center",
                     chip_cap_pf=28.5, board_margin=1.5)
    out = build(sp, outdir=str(tmp_path), name="unit", verbose=False)
    for key in ("dxf", "kicad_mod", "json"):
        assert key in out["files"], f"{key} was not written"
        assert os.path.getsize(out["files"][key]) > 0


def test_dxf_has_the_expected_layers(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    sp = AntennaSpec(**ST, board_margin=1.0)
    out = build(sp, outdir=str(tmp_path), name="layers", kicad=False,
                preview=False, verbose=False)
    doc = ezdxf.readfile(out["files"]["dxf"])
    assert doc.header["$INSUNITS"] == 4, "DXF must declare millimetres"
    layers = {e.dxf.layer for e in doc.modelspace()}
    for expected in ("ANT_CENTERLINE", "ANT_COPPER", "ANT_PADS", "ANT_VIA",
                     "BOARD_OUTLINE", "ANT_INFO"):
        assert expected in layers, f"missing layer {expected}"


def test_dxf_passes_an_audit(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    from ezdxf.audit import Auditor
    sp = AntennaSpec(**ST, shape="rrect", corner_r=5.9)
    out = build(sp, outdir=str(tmp_path), name="audit", kicad=False,
                preview=False, verbose=False)
    doc = ezdxf.readfile(out["files"]["dxf"])
    auditor = Auditor(doc)
    auditor.run()
    assert not auditor.errors


def test_kicad_footprint_is_balanced_sexpr(tmp_path):
    sp = AntennaSpec(**ST, term_pos="center")
    out = build(sp, outdir=str(tmp_path), name="kicad", preview=False,
                verbose=False)
    text = open(out["files"]["kicad_mod"], encoding="utf-8").read()
    assert text.count("(") == text.count(")")
    assert text.startswith("(footprint")
    assert '(layer "F.Cu")' in text
    assert text.count("fp_line") > 4 * sp.turns


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
