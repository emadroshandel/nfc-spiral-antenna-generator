#!/usr/bin/env python3
"""
Assemble the static site that GitHub Pages serves (used by .github/workflows/pages.yml).

    python scripts/build_site.py            # -> _site/
    python scripts/build_site.py --out dist --no-wheel

Layout of the result (the same relative layout server.py uses locally):

    _site/index.html         redirect to web/ (keeps ?query and #design links)
    _site/web/...            the interface
    _site/nfc_spiral.py      the engine, loaded into Pyodide at run time
    _site/nfc_api.py         the JSON interface the page calls
    _site/examples/*.json    presets
    _site/wheels/            a pinned pure-Python ezdxf wheel, so the page does
                             not depend on PyPI being reachable at run time
    _site/sitemap.xml        for Google Search Console (dated on every build)
    _site/google*.html       any Search Console verification file in the repo root
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_URL = "https://emadroshandel.github.io/nfc-spiral-antenna-generator/"

# Pages listed in the sitemap: (path under SITE_URL, change frequency, priority).
# Only real pages - not the root, which just redirects to web/.
SITEMAP_PAGES = [
    ("web/", "monthly", "1.0"),
]


def write_sitemap(out: str) -> None:
    today = datetime.date.today().isoformat()
    rows = "".join(
        f"  <url>\n    <loc>{SITE_URL}{path}</loc>\n    <lastmod>{today}</lastmod>\n"
        f"    <changefreq>{freq}</changefreq>\n    <priority>{prio}</priority>\n  </url>\n"
        for path, freq, prio in SITEMAP_PAGES)
    with open(os.path.join(out, "sitemap.xml"), "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                 + rows + "</urlset>\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "_site"))
    ap.add_argument("--no-wheel", action="store_true",
                    help="skip bundling ezdxf (the page then installs it from PyPI)")
    ap.add_argument("--ezdxf", default="ezdxf>=1.0,<2",
                    help="requirement used for the bundled wheel")
    a = ap.parse_args(argv)

    out = os.path.abspath(a.out)
    if os.path.isdir(out):
        shutil.rmtree(out)
    shutil.copytree(os.path.join(ROOT, "web"), os.path.join(out, "web"))
    for f in ("index.html", "nfc_spiral.py", "nfc_api.py", "LICENSE"):
        shutil.copy2(os.path.join(ROOT, f), out)
    os.makedirs(os.path.join(out, "examples"))
    for f in glob.glob(os.path.join(ROOT, "examples", "*.json")):
        shutil.copy2(f, os.path.join(out, "examples"))
    open(os.path.join(out, ".nojekyll"), "w").close()
    write_sitemap(out)
    for f in glob.glob(os.path.join(ROOT, "google*.html")):     # Search Console
        shutil.copy2(f, out)

    if not a.no_wheel:
        wdir = os.path.join(out, "wheels")
        os.makedirs(wdir)
        subprocess.check_call([
            sys.executable, "-m", "pip", "download", "--quiet", "--no-deps",
            "--only-binary=:all:", "--platform", "any", "--implementation", "py",
            "--python-version", "3.13", "--dest", wdir, a.ezdxf])
        wheels = sorted(glob.glob(os.path.join(wdir, "ezdxf-*-py3-none-any.whl")))
        if not wheels:
            sys.exit("no pure-Python ezdxf wheel found")
        with open(os.path.join(wdir, "index.json"), "w") as fh:
            json.dump({"ezdxf": os.path.basename(wheels[-1])}, fh)
        print("bundled", os.path.basename(wheels[-1]))

    n = sum(len(fs) for _, _, fs in os.walk(out))
    print(f"site assembled in {out} ({n} files)")


if __name__ == "__main__":
    main()
