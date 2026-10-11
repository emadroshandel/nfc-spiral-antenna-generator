/* NFC Spiral Antenna Generator - dual-mode boot.
 *
 * The same interface runs three ways:
 *
 *   desktop   desktop.py starts server.py and shows this page in a native
 *             window (pywebview); files are saved through a native dialog
 *   server    python server.py serves this page and answers /api/* over HTTP
 *   browser   the page is opened from GitHub Pages with no server at all;
 *             Pyodide runs the identical nfc_spiral.py + nfc_api.py inside
 *             the browser and NFC.call() goes to it directly
 *
 * app.js only ever calls NFC.call(name, payload) and never learns which.
 */
'use strict';

window.NFC = (function () {
  // Served by server.py the page sits at "/", on GitHub Pages it sits at
  // /<repo>/web/, so files one level up need a "../" prefix there.
  const inWeb = /\/web(\/|\/index\.html)?$/.test(location.pathname);
  const base = inWeb ? '../' : '';
  const qs = new URLSearchParams(location.search);

  // Pyodide release the browser engine uses. ?pyodide=/path/ (same-origin
  // only) points it at a local copy for offline testing.
  let PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/';
  const override = qs.get('pyodide');
  if (override && override.startsWith('/') && !override.startsWith('//')) {
    PYODIDE = override.endsWith('/') ? override : override + '/';
  }

  const NFC = {
    base,
    mode: 'unknown',
    asset: p => base + p,
    call: null,          // async (name, payload) -> result object
    ready: null,         // Promise, resolves once call() works
    isDesktop: () => !!(window.pywebview && window.pywebview.api &&
                        window.pywebview.api.save_file),
  };

  // ------------------------------------------------------------ splash ----
  function splash(msg, sub, pct) {
    if (msg) document.getElementById('bootmsg').textContent = msg;
    if (sub !== undefined) document.getElementById('bootsub').textContent = sub;
    if (pct !== undefined) document.getElementById('bootbar').style.width = pct + '%';
  }
  NFC.splash = splash;
  NFC.hideSplash = () => {
    const el = document.getElementById('boot');
    if (!el) return;
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 320);
  };

  // ------------------------------------------------------- server mode ----
  async function haveServer() {
    if (location.protocol === 'file:' || qs.get('engine') === 'browser') return false;
    if (/\.github\.io$/.test(location.hostname)) return false;   // static hosting, no API
    try {
      const r = await fetch('api/health', { cache: 'no-store' });
      if (!r.ok) return false;
      const j = await r.json();
      return !!(j && j.ok);
    } catch (e) { return false; }
  }

  async function serverCall(name, payload) {
    const r = await fetch('api/' + name, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    let j;
    try { j = await r.json(); } catch (e) { throw new Error('Server returned ' + r.status); }
    return j;
  }

  // ------------------------------------------------------ browser mode ----
  async function bootPyodide() {
    if (location.protocol === 'file:') {
      throw new Error('Opened as a local file. Start the app with "Run NFC Antenna" ' +
                      '(or python server.py), or use the online version.');
    }
    splash('Starting the calculation engine in your browser…',
           'First visit downloads Python for WebAssembly (~15 MB); it is cached afterwards.', 6);
    await new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = PYODIDE + 'pyodide.js';
      s.onload = res;
      s.onerror = () => rej(new Error('Could not load Pyodide from ' + PYODIDE));
      document.head.appendChild(s);
    });

    splash('Loading Python…', undefined, 22);
    const py = await loadPyodide({ indexURL: PYODIDE });

    splash('Loading shapely…', 'copper outline and clearance check', 45);
    await py.loadPackage(['shapely', 'micropip']);

    splash('Installing ezdxf…', 'DXF writer', 66);
    const micropip = py.pyimport('micropip');
    let wheel = null;
    try {        // a copy bundled with the site by the Pages workflow
      const r = await fetch(NFC.asset('wheels/index.json'), { cache: 'no-store' });
      if (r.ok) wheel = (await r.json()).ezdxf;
    } catch (e) { /* not bundled - fall back to PyPI */ }
    try {
      await micropip.install(wheel ? new URL(NFC.asset('wheels/' + wheel), location.href).href : 'ezdxf');
    } catch (e) {
      if (!wheel) throw e;
      await micropip.install('ezdxf');
    }

    splash('Loading the generator…', '', 84);
    for (const f of ['nfc_spiral.py', 'nfc_api.py']) {
      const r = await fetch(NFC.asset(f), { cache: 'no-cache' });
      if (!r.ok) throw new Error('Missing ' + f + ' (' + r.status + ')');
      py.FS.writeFile('/home/pyodide/' + f, await r.text());
    }
    await py.runPythonAsync(`
import sys
sys.path.insert(0, "/home/pyodide")
import nfc_api
`);
    const dispatch = py.globals.get('nfc_api').dispatch_json;
    NFC.call = async (name, payload) => JSON.parse(dispatch(name, JSON.stringify(payload || {})));
    NFC.mode = 'browser';
  }

  NFC.ready = (async () => {
    splash('Starting…', '', 3);
    if (await haveServer()) {
      NFC.call = serverCall;
      NFC.mode = 'server';
      // pywebview injects its bridge shortly after load
      if (!NFC.isDesktop()) {
        await new Promise(res => {
          const t = setTimeout(res, 400);
          window.addEventListener('pywebviewready', () => { clearTimeout(t); res(); }, { once: true });
        });
      }
      if (NFC.isDesktop()) NFC.mode = 'desktop';
      splash('Ready', '', 100);
      return;
    }
    try {
      await bootPyodide();
      splash('Ready', '', 100);
    } catch (err) {
      splash('The calculation engine could not start.', (err && err.message) || String(err), 100);
      document.getElementById('boot').classList.add('failed');
      throw err;
    }
  })();

  return NFC;
})();
