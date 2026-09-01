#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 nfc_spiral_gui.py  --  desktop front end for nfc_spiral.py
================================================================================

Tkinter GUI with a live copper preview. Every parameter updates the drawing and
the electrical numbers as you type; nothing is written to disk until you press
an export button.

    python nfc_spiral_gui.py

Tkinter ships with CPython on Windows and macOS. On Debian/Ubuntu it is a
separate package:  sudo apt install python3-tk
================================================================================
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import asdict

# --- make sure nfc_spiral.py is importable even when launched from elsewhere --
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:                                        # pragma: no cover
    sys.exit("Tkinter is not available.\n"
             "  Debian/Ubuntu:  sudo apt install python3-tk\n"
             "  Fedora:         sudo dnf install python3-tkinter\n"
             "  Windows/macOS:  reinstall Python with the Tcl/Tk option ticked")

import nfc_spiral as ns
from nfc_spiral import AntennaSpec

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import (
        FigureCanvasTkAgg, NavigationToolbar2Tk)
    from matplotlib.figure import Figure
    from matplotlib.patches import Polygon as MplPoly, Circle
    HAVE_MPL = True
except ImportError:                                        # pragma: no cover
    HAVE_MPL = False


PCB_GREEN = "#0f3a25"
COPPER = "#b87333"
COPPER_EDGE = "#7a4a1f"
PAD_GOLD = "#e8c33a"
BOTTOM_BLUE = "#4da3ff"


class SpiralGUI(ttk.Frame):

    # (attribute, label, kind, options//width)  kind: num | int | choice | text
    GEOMETRY = [
        ("shape",        "Shape",              "choice", ["rect", "rrect",
                                                          "octagon", "circle"]),
        ("length",       "Outer length X (mm)", "num",   None),
        ("width",        "Outer width Y (mm)",  "num",   None),
        ("turns",        "Turns",               "int",   None),
        ("trace",        "Conductor width (mm)", "num",  None),
        ("gap",          "Turn-to-turn gap (mm)", "num", None),
        ("corner_r",     "Corner radius (mm)",  "num",   None),
        ("thickness_um", "Copper thickness (um)", "num", None),
    ]
    TERMINALS = [
        ("term_pos",   "Terminal position",   "text",   None),
        ("pad_side",   "Return pad side",     "choice", ["auto", "left", "right"]),
        ("lead_out",   "Lead-out length (mm)", "num",   None),
        ("pad_w",      "Pad width (mm)",       "num",   None),
        ("pad_h",      "Pad height (mm)",      "num",   None),
        ("pad_pitch",  "Pad pitch (mm)",       "num",   None),
        ("via_pad",    "Via land (mm)",        "num",   None),
        ("via_drill",  "Via drill (mm)",       "num",   None),
    ]
    ELECTRICAL = [
        ("freq_mhz",         "Frequency (MHz)",  "num",    None),
        ("chip_cap_pf",      "Chip capacitance (pF)", "num", None),
        ("proximity_factor", "Proximity factor", "num",    None),
        ("model",            "Inductance model", "choice",
         ["auto", "greenhouse", "mohan"]),
    ]

    def __init__(self, master):
        super().__init__(master, padding=6)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self.vars: dict[str, tk.Variable] = {}
        self._job = None
        self.spec: AntennaSpec | None = None
        self.geo = None
        self.res: dict = {}
        self.last_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "output")

        self._build_controls()
        self._build_canvas()
        self.refresh()

    # ------------------------------------------------------------- controls --
    def _build_controls(self):
        outer = ttk.Frame(self)
        outer.grid(row=0, column=0, sticky="ns", padx=(0, 8))

        canvas = tk.Canvas(outer, width=320, highlightthickness=0,
                           borderwidth=0)
        vbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        panel = ttk.Frame(canvas)
        panel.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=panel, anchor="nw")
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        def _wheel(evt):
            step = -1 if getattr(evt, "delta", 0) > 0 or evt.num == 4 else 1
            canvas.yview_scroll(step, "units")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind_all(seq, _wheel)

        D = {f: getattr(AntennaSpec, f, None) for f in ()}    # placeholder
        defaults = AntennaSpec()

        row = 0
        for title, spec_rows in (("Geometry", self.GEOMETRY),
                                 ("Terminals & pads", self.TERMINALS),
                                 ("Electrical", self.ELECTRICAL)):
            box = ttk.LabelFrame(panel, text=title, padding=6)
            box.grid(row=row, column=0, sticky="ew", pady=(0, 6), padx=2)
            box.columnconfigure(1, weight=1)
            row += 1
            for r, (attr, label, kind, choices) in enumerate(spec_rows):
                ttk.Label(box, text=label).grid(row=r, column=0, sticky="w",
                                                pady=1)
                val = getattr(defaults, attr)
                if kind == "choice":
                    v = tk.StringVar(value=str(val))
                    w = ttk.Combobox(box, textvariable=v, values=choices,
                                     state="readonly", width=12)
                    w.bind("<<ComboboxSelected>>", lambda e: self.schedule())
                else:
                    v = tk.StringVar(value=str(val))
                    w = ttk.Entry(box, textvariable=v, width=14)
                    w.bind("<KeyRelease>", lambda e: self.schedule())
                w.grid(row=r, column=1, sticky="ew", pady=1)
                self.vars[attr] = v

        # ---- toggles
        box = ttk.LabelFrame(panel, text="Options", padding=6)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6), padx=2)
        row += 1
        for r, (attr, label) in enumerate([
                ("make_bridge", "Draw bottom-layer return bridge"),
                ("hatch_copper", "Solid copper hatch in DXF"),
                ("include_leads", "Count leads in the L model")]):
            v = tk.BooleanVar(value=getattr(defaults, attr))
            ttk.Checkbutton(box, text=label, variable=v,
                            command=self.schedule).grid(row=r, column=0,
                                                        sticky="w")
            self.vars[attr] = v
        v = tk.StringVar(value=str(defaults.board_margin))
        ttk.Label(box, text="Board margin (mm, <0 = none)").grid(row=3,
                                                                 column=0,
                                                                 sticky="w",
                                                                 pady=(4, 0))
        e = ttk.Entry(box, textvariable=v, width=14)
        e.grid(row=4, column=0, sticky="ew")
        e.bind("<KeyRelease>", lambda ev: self.schedule())
        self.vars["board_margin"] = v

        # ---- helpers
        box = ttk.LabelFrame(panel, text="Helpers", padding=6)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6), padx=2)
        box.columnconfigure(0, weight=1)
        row += 1
        ttk.Button(box, text="Corner radius → auto",
                   command=self.auto_corner).grid(row=0, column=0, sticky="ew",
                                                  pady=1)
        f = ttk.Frame(box)
        f.grid(row=1, column=0, sticky="ew", pady=1)
        f.columnconfigure(0, weight=1)
        self.target_l = tk.StringVar(value="2.5")
        ttk.Entry(f, textvariable=self.target_l, width=6).grid(row=0, column=1)
        ttk.Button(f, text="Solve turns for L (uH) =",
                   command=self.solve_turns).grid(row=0, column=0, sticky="ew")
        ttk.Button(box, text="Measure copper clearance (DRC)",
                   command=self.run_drc).grid(row=2, column=0, sticky="ew",
                                              pady=1)

        # ---- export
        box = ttk.LabelFrame(panel, text="Export", padding=6)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6), padx=2)
        box.columnconfigure(0, weight=1)
        row += 1
        self.basename = tk.StringVar(value="nfc_antenna")
        ttk.Label(box, text="File base name").grid(row=0, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.basename).grid(row=1, column=0,
                                                        sticky="ew")
        ttk.Button(box, text="Export all (DXF + KiCad + JSON + PNG)",
                   command=lambda: self.export("all")).grid(row=2, column=0,
                                                            sticky="ew",
                                                            pady=(4, 1))
        ttk.Button(box, text="Export DXF only…",
                   command=lambda: self.export("dxf")).grid(row=3, column=0,
                                                            sticky="ew", pady=1)
        ttk.Button(box, text="Load settings…",
                   command=self.load_json).grid(row=4, column=0, sticky="ew",
                                                pady=(6, 1))

    # --------------------------------------------------------------- canvas --
    def _build_canvas(self):
        right = ttk.Frame(self)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        if HAVE_MPL:
            self.fig = Figure(figsize=(8.2, 5.4), dpi=100)
            self.ax = self.fig.add_subplot(111)
            self.canvas = FigureCanvasTkAgg(self.fig, master=right)
            self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
            bar = ttk.Frame(right)
            bar.grid(row=1, column=0, sticky="ew")
            NavigationToolbar2Tk(self.canvas, bar).update()
        else:
            ttk.Label(right, text="matplotlib not installed - no preview.\n"
                                  "pip install matplotlib",
                      anchor="center").grid(row=0, column=0, sticky="nsew")

        self.results = tk.Text(right, height=12, wrap="none",
                               font=("Consolas", 9))
        self.results.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.status = ttk.Label(right, text="", anchor="w", foreground="#a33")
        self.status.grid(row=3, column=0, sticky="ew")

    # ----------------------------------------------------------- spec build --
    def read_spec(self) -> AntennaSpec:
        d = asdict(AntennaSpec())
        for attr, var in self.vars.items():
            raw = var.get()
            if isinstance(var, tk.BooleanVar):
                d[attr] = bool(raw)
            elif attr in ("shape", "model", "term_pos", "pad_side"):
                d[attr] = str(raw)
            elif attr == "turns":
                d[attr] = int(float(raw))
            else:
                d[attr] = float(raw)
        return AntennaSpec(**d)

    def schedule(self, _evt=None):
        """Debounce: redraw 220 ms after the last keystroke."""
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(220, self.refresh)

    # -------------------------------------------------------------- refresh --
    def refresh(self):
        self._job = None
        try:
            sp = self.read_spec()
        except (ValueError, TypeError):
            self.status.config(text="waiting for a valid number…")
            return
        errs = sp.validate()
        hard = [e for e in errs if e.startswith("error:")]
        if hard:
            self.status.config(
                text=" | ".join(e.split(": ", 1)[-1] for e in hard))
            return
        try:
            self.spec = sp
            self.geo = ns.build_geometry(sp)
            self.res = ns.electrical(sp, self.geo)
        except Exception as exc:                            # pragma: no cover
            self.status.config(text=f"{type(exc).__name__}: {exc}")
            return
        warns = [e.split(": ", 1)[-1] for e in errs if e.startswith("warn:")]
        self.status.config(text=" | ".join(warns))
        self.draw()
        self.show_results()

    def draw(self):
        if not HAVE_MPL:
            return
        sp, g = self.spec, self.geo
        ax = self.ax
        ax.clear()
        polys = ns.copper_polygons([(g.top_path, sp.trace)])
        if polys:
            for ext, holes in polys:
                ax.add_patch(MplPoly(ext, closed=True, facecolor=COPPER,
                                     edgecolor=COPPER_EDGE, lw=0.3, zorder=2))
                for h in holes:
                    ax.add_patch(MplPoly(h, closed=True, facecolor=PCB_GREEN,
                                         edgecolor=COPPER_EDGE, lw=0.3,
                                         zorder=3))
        else:
            xs, ys = zip(*g.top_path)
            ax.plot(xs, ys, color=COPPER, lw=1.5, zorder=2)
        if g.bridge:
            xs, ys = zip(*g.bridge)
            ax.plot(xs, ys, color=BOTTOM_BLUE, lw=max(1.3, sp.trace * 2.4),
                    ls=(0, (5, 2)), zorder=5, label="bottom layer")
        ax.add_patch(Circle(g.via, sp.via_pad / 2, facecolor="#d8d8d8",
                            edgecolor="k", lw=0.4, zorder=6))
        pads = [g.pad_outer] + ([g.pad_return] if sp.make_bridge else [])
        for c in pads:
            ax.add_patch(MplPoly(
                [(c[0] - sp.pad_w / 2, c[1] - sp.pad_h / 2),
                 (c[0] + sp.pad_w / 2, c[1] - sp.pad_h / 2),
                 (c[0] + sp.pad_w / 2, c[1] + sp.pad_h / 2),
                 (c[0] - sp.pad_w / 2, c[1] + sp.pad_h / 2)],
                closed=True, facecolor=PAD_GOLD, edgecolor="k", lw=0.4,
                zorder=6))
        if sp.board_margin >= 0:
            m = sp.board_margin
            hx, hy = sp.length / 2, sp.width / 2
            y0 = -hy - sp.lead_out - sp.pad_h / 2 - m
            ax.add_patch(MplPoly([(-hx - m, y0), (hx + m, y0),
                                  (hx + m, hy + m), (-hx - m, hy + m)],
                                 closed=True, fill=False, edgecolor="#dddddd",
                                 lw=0.9, ls="--", zorder=1))
        ax.set_facecolor(PCB_GREEN)
        ax.set_aspect("equal")
        pad = 0.06 * max(sp.length, sp.width) + 2
        ymin = -sp.width / 2 - sp.lead_out - sp.pad_h - pad
        ax.set_xlim(-sp.length / 2 - pad, sp.length / 2 + pad)
        ax.set_ylim(ymin, sp.width / 2 + pad)
        ax.set_xlabel("x [mm]")
        ax.set_ylabel("y [mm]")
        ax.grid(alpha=0.15, color="w")
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def show_results(self):
        r, sp = self.res, self.spec
        lines = [
            f"  L  = {r['L_uH']:.4f} uH        ({r['model_used']} model)",
        ]
        if not _isnan(r["L_ideal_closed_turns_uH"]):
            lines.append(f"       ideal closed turns "
                         f"{r['L_ideal_closed_turns_uH']:.4f} uH   "
                         f"Mohan {r['L_mohan_uH']:.4f} uH")
        else:
            lines.append(f"       Mohan {r['L_mohan_uH']:.4f} uH")
        lines += [
            f"  R  = {r['R_dc_ohm']:.4f} ohm DC   {r['R_ac_ohm']:.4f} ohm AC "
            f"(skin depth {r['skin_depth_um']:.1f} um)",
            f"  Q  = {r['Q']:.1f}  @ {sp.freq_mhz:g} MHz",
            f"  C  = {r['C_total_resonant_pF']:.2f} pF total for resonance"
            + (f"   ->  {r['C_external_pF']:.2f} pF external "
               f"(chip {r['C_chip_pF']:g} pF)" if "C_external_pF" in r else ""),
            "",
            f"  conductor {r['conductor_length_mm']:.1f} mm    "
            f"inner window "
            f"{sp.length - 2*(sp.turns*sp.trace+(sp.turns-1)*sp.gap):.2f} x "
            f"{sp.width - 2*(sp.turns*sp.trace+(sp.turns-1)*sp.gap):.2f} mm",
            f"  terminal x = {sp.term_x:+.2f} mm   "
            f"(range {sp.term_x_min:+.2f} .. {sp.term_x_max:+.2f})",
        ]
        if sp.shape == "rrect":
            lines.append(f"  corner radius {sp.corner_r:g} mm   "
                         f"(round every turn: {sp.min_corner_r_all_rounded:.2f}"
                         f" .. {sp.max_corner_r:.2f} mm)")
        if "min_clearance_mm" in self.res:
            lines.append(f"  measured clearance "
                         f"{self.res['min_clearance_mm']:.4f} mm "
                         f"(requested {sp.gap:g} mm)")
        self.results.delete("1.0", "end")
        self.results.insert("1.0", "\n".join(lines))

    # -------------------------------------------------------------- helpers --
    def auto_corner(self):
        try:
            sp = self.read_spec()
        except ValueError:
            return
        if sp.shape not in ("rect", "rrect"):
            messagebox.showinfo("Corner radius",
                                "Corner rounding applies to the rectangular "
                                "shapes only.")
            return
        self.vars["shape"].set("rrect")
        r = 0.5 * (sp.min_corner_r_all_rounded + sp.max_corner_r)
        self.vars["corner_r"].set(f"{r:.3f}")
        self.refresh()

    def solve_turns(self):
        try:
            sp = self.read_spec()
            target = float(self.target_l.get())
        except ValueError:
            return
        n = ns.solve_turns(sp, target, verbose=False)
        if n is None:
            messagebox.showwarning("Solve turns",
                                   "No turn count is realisable for this "
                                   "outline, trace width and gap.")
            return
        self.vars["turns"].set(str(n))
        self.refresh()
        messagebox.showinfo(
            "Solve turns",
            f"{n} turns gives {self.res['L_uH']:.3f} uH "
            f"(target {target:g} uH).")

    def run_drc(self):
        if self.geo is None:
            return
        sp = self.spec
        c = ns.measure_clearance(self.geo.top_path, sp.trace, sp.gap)
        self.res["min_clearance_mm"] = c
        self.show_results()
        if _isnan(c):
            messagebox.showwarning("Clearance", "shapely is not installed, so "
                                                "clearance cannot be measured.")
        elif c < 0.98 * sp.gap:
            messagebox.showwarning(
                "Clearance",
                f"Measured copper clearance is {c:.4f} mm, below the "
                f"requested {sp.gap:g} mm.\n\n"
                "For a rounded spiral this usually means the corner radius is "
                "too small, so inner turns fall back to sharp corners. Press "
                "'Corner radius -> auto'.")
        else:
            messagebox.showinfo("Clearance",
                                f"Minimum copper clearance {c:.4f} mm "
                                f"(requested {sp.gap:g} mm). Pass.")

    # --------------------------------------------------------------- export --
    def export(self, what):
        if self.spec is None:
            return
        outdir = filedialog.askdirectory(
            title="Choose an output folder",
            initialdir=self.last_dir if os.path.isdir(self.last_dir)
            else os.path.expanduser("~"))
        if not outdir:
            return
        self.last_dir = outdir
        name = self.basename.get().strip() or "nfc_antenna"
        try:
            out = ns.build(self.spec, outdir=outdir, name=name,
                           dxf=True, kicad=(what == "all"),
                           preview=(what == "all"), strict=False,
                           verbose=False)
        except Exception:                                   # pragma: no cover
            messagebox.showerror("Export failed", traceback.format_exc())
            return
        self.res.update(out["results"])
        self.show_results()
        messagebox.showinfo(
            "Export complete",
            "Written:\n\n" + "\n".join(out["files"].values()))

    def load_json(self):
        path = filedialog.askopenfilename(
            title="Load a previously exported .json",
            filetypes=[("Antenna settings", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                params = json.load(fh)["parameters"]
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            return
        for attr, var in self.vars.items():
            if attr in params:
                var.set(params[attr] if isinstance(var, tk.BooleanVar)
                        else str(params[attr]))
        self.refresh()


def _isnan(x) -> bool:
    return x != x


def main():
    root = tk.Tk()
    root.title("NFC spiral antenna generator")
    root.geometry("1240x860")
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:                                     # pragma: no cover
        pass
    SpiralGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
