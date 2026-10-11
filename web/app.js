/* NFC Spiral Antenna Generator - interface.
 * Talks to the engine only through NFC.call(name, payload) (see boot.js).
 */
'use strict';

(function () {
  const $ = id => document.getElementById(id);
  const ONLINE = 'https://emadroshandel.github.io/nfc-spiral-antenna-generator/web/';

  // ------------------------------------------------------------ fields ----
  // [key, label, kind, extra]  kind: num | int | choice | text | bool
  const GROUPS = [
    ['Geometry', true, [
      ['shape', 'Shape', 'choice', [['rect', 'rectangular'], ['rrect', 'rounded rect'],
                                    ['octagon', 'octagon'], ['circle', 'circle']]],
      ['length', 'Outer length X', 'num', { unit: 'mm', step: 0.1, min: 1 }],
      ['width', 'Outer width Y', 'num', { unit: 'mm', step: 0.1, min: 1 }],
      ['turns', 'Turns', 'int', { step: 1, min: 1, max: 60 }],
      ['trace', 'Conductor width', 'num', { unit: 'mm', step: 0.01, min: 0.01 }],
      ['gap', 'Turn-to-turn gap', 'num', { unit: 'mm', step: 0.01, min: 0.01 }],
      ['corner_r', 'Corner radius', 'num', { unit: 'mm', step: 0.1, min: 0, only: 'rrect' }],
      ['thickness_um', 'Copper thickness', 'num', { unit: 'µm', step: 1, min: 1,
        hint: '35 µm = 1 oz, 18 µm = ½ oz, 70 µm = 2 oz' }],
    ]],
    ['Terminals & pads', false, [
      ['term_pos', 'Terminal position', 'text', { hint: 'right · center · left · or an x coordinate in mm' }],
      ['pad_side', 'Return pad side', 'choice', [['auto', 'auto'], ['left', 'left'], ['right', 'right']]],
      ['lead_out', 'Lead-out length', 'num', { unit: 'mm', step: 0.1, min: 0 }],
      ['pad_w', 'Pad width', 'num', { unit: 'mm', step: 0.1, min: 0.1 }],
      ['pad_h', 'Pad height', 'num', { unit: 'mm', step: 0.1, min: 0.1 }],
      ['pad_pitch', 'Pad pitch', 'num', { unit: 'mm', step: 0.1, min: 0.1 }],
      ['via_pad', 'Via land Ø', 'num', { unit: 'mm', step: 0.05, min: 0.1 }],
      ['via_drill', 'Via drill Ø', 'num', { unit: 'mm', step: 0.05, min: 0.05 }],
      ['make_bridge', 'Bottom-layer return bridge', 'bool'],
    ]],
    ['Electrical', true, [
      ['freq_mhz', 'Frequency', 'num', { unit: 'MHz', step: 0.01, min: 0.01 }],
      ['chip_cap_pf', 'Chip capacitance', 'num', { unit: 'pF', step: 0.1, min: 0,
        hint: 'from the tag / reader IC datasheet; 0 = none' }],
      ['proximity_factor', 'Proximity factor', 'num', { step: 0.1, min: 1,
        hint: 'multiplies R_ac; 1.3–2.5 for a tight pitch' }],
      ['model', 'Inductance model', 'choice', [['auto', 'auto'], ['greenhouse', 'Greenhouse'], ['mohan', 'Mohan']]],
      ['include_leads', 'Count the leads in L', 'bool'],
    ]],
    ['Board & output', false, [
      ['board_margin', 'Board margin', 'num', { unit: 'mm', step: 0.5, hint: 'negative = no board outline' }],
      ['hatch_copper', 'Solid copper hatch in the DXF', 'bool'],
      ['arc_segments', 'Segments per 90° arc', 'int', { step: 1, min: 2, max: 90 }],
      ['circle_segments', 'Samples per turn (circle)', 'int', { step: 1, min: 8, max: 720 }],
    ]],
  ];
  const FIELDS = GROUPS.flatMap(g => g[2]);
  const KIND = Object.fromEntries(FIELDS.map(f => [f[0], f[2]]));

  // first-run design: the ST reference outline with rounded corners
  const START = { shape: 'rrect', corner_r: 5.915, term_pos: 'center', chip_cap_pf: 28.5, board_margin: 1.5 };

  const EXAMPLES = [
    ['st_reference', 'ST reference 46×20 mm, 6 turns'],
    ['tag_rounded_center', 'Rounded tag, centred terminal'],
    ['tag_40x30', 'Tag 40×30 mm, cheap 2-layer rules'],
    ['reader_circular_35', 'Circular reader coil Ø35 mm'],
    ['octagon_30', 'Octagon 30 mm, 7 turns'],
  ];

  let DEFAULTS = {};
  let last = null;            // last successful generate() result
  let seq = 0;                // drops stale responses
  let timer = null;
  let sweepKey = null, sweepData = null;
  const view = { box: null, world: null, worldKey: '' };

  // ------------------------------------------------------- formatting ----
  const fmt = (v, n = 3) => (v === null || v === undefined || Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(n);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function toast(html, ms = 3200) {
    const t = $('toast');
    t.innerHTML = html;
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.hidden = true; }, ms);
  }

  // ------------------------------------------------------------- form ----
  function buildForm() {
    const form = $('form');
    form.innerHTML = '';
    for (const [title, open, rows] of GROUPS) {
      const d = document.createElement('details');
      d.className = 'group';
      d.open = open;
      d.innerHTML = `<summary>${esc(title)}</summary>`;
      const checks = document.createElement('div');
      checks.className = 'checks';
      for (const [key, label, kind, extra] of rows) {
        if (kind === 'bool') {
          checks.insertAdjacentHTML('beforeend',
            `<label class="chk"><input type="checkbox" id="f_${key}"> ${esc(label)}</label>`);
          continue;
        }
        const x = (kind === 'choice' ? {} : extra) || {};
        const unit = x.unit ? ` <span class="unit">(${esc(x.unit)})</span>` : '';
        let ctl;
        if (kind === 'choice') {
          ctl = `<select id="f_${key}">${extra.map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join('')}</select>`;
        } else if (kind === 'text') {
          ctl = `<input id="f_${key}" type="text" spellcheck="false">`;
        } else {
          ctl = `<input id="f_${key}" type="number" inputmode="decimal" step="${x.step || 'any'}"` +
                (x.min !== undefined ? ` min="${x.min}"` : '') + (x.max !== undefined ? ` max="${x.max}"` : '') + '>';
        }
        const row = document.createElement('div');
        row.className = 'row';
        row.id = 'r_' + key;
        row.innerHTML = `<label for="f_${key}">${esc(label)}${unit}</label>${ctl}`;
        d.appendChild(row);
        if (x.hint) d.insertAdjacentHTML('beforeend', `<p class="hint" id="h_${key}">${esc(x.hint)}</p>`);
      }
      if (checks.children.length) d.appendChild(checks);
      form.appendChild(d);
    }
    form.addEventListener('input', onEdit);
    form.addEventListener('change', onEdit);
  }

  function setForm(p) {
    for (const [key, , kind] of FIELDS) {
      if (!(key in p)) continue;
      const el = $('f_' + key);
      if (kind === 'bool') el.checked = !!p[key];
      else el.value = p[key];
    }
    syncVisibility();
  }

  function readForm() {
    const p = {};
    let bad = false;
    for (const [key, , kind] of FIELDS) {
      const el = $('f_' + key);
      if (kind === 'bool') { p[key] = el.checked; continue; }
      if (kind === 'num' || kind === 'int') {
        const v = el.value.trim() === '' ? NaN : Number(el.value);
        const ok = Number.isFinite(v);
        el.classList.toggle('bad', !ok);
        if (!ok) bad = true;
        p[key] = kind === 'int' ? Math.round(v) : v;
      } else {
        p[key] = el.value;
      }
    }
    return bad ? null : p;
  }

  function syncVisibility() {
    const shape = $('f_shape').value;
    $('r_corner_r').hidden = shape !== 'rrect';
    $('r_circle_segments').hidden = shape !== 'circle';
    const square = shape === 'circle' || shape === 'octagon';
    $('r_width').querySelector('label').firstChild.textContent = square ? 'Outer width Y (= length) ' : 'Outer width Y ';
  }

  function onEdit(e) {
    if (e && e.target && e.target.id === 'f_shape') {
      const shape = e.target.value;
      if (shape === 'circle' || shape === 'octagon') $('f_width').value = $('f_length').value;
      if (shape === 'rrect' && !(Number($('f_corner_r').value) > 0)) { autoCorner(); return; }
      syncVisibility();
    }
    if (e && e.target && e.target.id === 'f_length') {
      const s = $('f_shape').value;
      if (s === 'circle' || s === 'octagon') $('f_width').value = $('f_length').value;
    }
    schedule();
  }

  function schedule(ms = 220) {
    clearTimeout(timer);
    $('kpis').classList.add('stale');
    timer = setTimeout(refresh, ms);
  }

  // ----------------------------------------------------- state saving ----
  const b64e = s => btoa(unescape(encodeURIComponent(s))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const b64d = s => decodeURIComponent(escape(atob(s.replace(/-/g, '+').replace(/_/g, '/'))));

  function diffFromDefaults(p) {
    const d = {};
    for (const k in p) if (p[k] !== DEFAULTS[k]) d[k] = p[k];
    return d;
  }
  function remember(p) {
    try { localStorage.setItem('nfc.params', JSON.stringify(diffFromDefaults(p))); } catch (e) {}
  }
  function initialParams() {
    const m = location.hash.match(/[#&]d=([^&]+)/);
    if (m) {
      try { return { ...DEFAULTS, ...JSON.parse(b64d(m[1])) }; } catch (e) { /* bad link */ }
    }
    try {
      const s = localStorage.getItem('nfc.params');
      if (s) return { ...DEFAULTS, ...JSON.parse(s) };
    } catch (e) {}
    return { ...DEFAULTS, ...START };
  }

  // ---------------------------------------------------------- refresh ----
  async function refresh() {
    const p = readForm();
    if (!p) { showMessages(['error: fix the highlighted fields']); return; }
    const my = ++seq;
    let out;
    try {
      out = await NFC.call('generate', { params: p });
    } catch (e) {
      out = { ok: false, errors: ['error: ' + e.message] };
    }
    if (my !== seq) return;
    $('kpis').classList.remove('stale');
    showMessages(out.errors || []);
    if (!out.ok) { last = null; return; }
    last = out;
    remember(p);
    if (location.hash) history.replaceState(null, '', location.pathname + location.search);
    drawKpis(out);
    drawLayout(out.geometry);
    drawDetails(out);
    if (!document.querySelector('[data-pane="sweep"]').hidden) loadSweep();
  }

  function showMessages(errs) {
    const box = $('messages');
    box.innerHTML = '';
    for (const e of errs) {
      const hard = e.startsWith('error:');
      box.insertAdjacentHTML('beforeend',
        `<div class="msg ${hard ? 'err' : 'warn'}">${esc(e.replace(/^(error|warn):\s*/, ''))}</div>`);
    }
  }

  // ------------------------------------------------------------- KPIs ----
  function drawKpis(o) {
    const r = o.results, p = o.params;
    const clrOk = r.min_clearance_mm === null || r.min_clearance_mm >= 0.98 * p.gap;
    const cExt = r.C_external_pF;
    const cards = [
      ['Inductance', `${fmt(r.L_uH, 3)}<small>µH</small>`, `${esc(r.model_used)} model`],
      ['Q', `${fmt(r.Q, 0)}`, `at ${fmt(r.f_MHz, 2)} MHz · R<sub>ac</sub> ${fmt(r.R_ac_ohm, 2)} Ω`],
      [cExt !== undefined && cExt !== null ? 'Tuning C (external)' : 'Resonating C',
        `${fmt(cExt !== undefined && cExt !== null ? cExt : r.C_total_resonant_pF, 2)}<small>pF</small>`,
        cExt !== undefined && cExt !== null ? `${fmt(r.C_total_resonant_pF, 2)} pF total − ${fmt(r.C_chip_pF, 1)} pF chip`
                                            : 'total, at the carrier'],
      ['Min clearance', `${fmt(r.min_clearance_mm, 3)}<small>mm</small>`,
        clrOk ? `meets the ${fmt(p.gap, 3)} mm gap` : `below the ${fmt(p.gap, 3)} mm gap`, clrOk ? 'pass' : 'fail'],
    ];
    if (cExt !== undefined && cExt !== null && cExt < 0) {
      cards[2][2] = 'chip C alone exceeds resonance — fewer turns or smaller coil';
      cards[2][3] = 'fail';
    }
    $('kpis').innerHTML = cards.map(([k, v, s, cls]) =>
      `<div class="kpi ${cls || ''}"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join('');
  }

  // ----------------------------------------------------------- layout ----
  const P = pt => `${pt[0]},${-pt[1]}`;
  const ring = r => 'M' + r.map(pt => pt[0] + ' ' + (-pt[1])).join('L') + 'Z';

  function worldBox(g) {
    const pts = [];
    for (const c of g.copper) pts.push(...c.outer);
    if (g.board) pts.push(...g.board);
    for (const c of g.pads) {
      pts.push([c[0] - g.pad_w / 2, c[1] - g.pad_h / 2], [c[0] + g.pad_w / 2, c[1] + g.pad_h / 2]);
    }
    pts.push(...g.keepout);
    const xs = pts.map(q => q[0]), ys = pts.map(q => -q[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const span = Math.max(x1 - x0, y1 - y0);
    const m = span * 0.09 + 1;
    return { x: x0 - m, y: y0 - m, w: x1 - x0 + 2 * m, h: y1 - y0 + 2 * m, span };
  }

  function layoutSvg(g, forExport) {
    const W = view.world;
    const s = W.span;
    const lw = Math.max(s / 900, 0.01);
    const show = id => forExport ? id !== 'vCenter' && $(id).checked : $(id).checked;
    let body = '';
    if (g.board) body += `<polygon class="board" points="${g.board.map(P).join(' ')}"/>`;
    if (show('vKeep')) body += `<polygon class="keep" points="${g.keepout.map(P).join(' ')}"/>`;
    body += `<path class="cu" d="${g.copper.map(c => ring(c.outer) + c.holes.map(ring).join('')).join('')}"/>`;
    if (show('vCenter')) body += `<polyline class="cl" points="${g.centerline.map(P).join(' ')}"/>`;
    if (show('vBridge') && g.bridge.length > 1) {
      body += `<polyline class="btm" points="${g.bridge.map(P).join(' ')}" style="stroke-width:${Math.max(g.trace, lw * 3)}"/>`;
    }
    body += `<circle class="via" cx="${g.via[0]}" cy="${-g.via[1]}" r="${g.via_pad / 2}"/>`;
    body += `<circle class="drill" cx="${g.via[0]}" cy="${-g.via[1]}" r="${g.via_drill / 2}"/>`;
    for (const c of g.pads) {
      body += `<rect class="pad" x="${c[0] - g.pad_w / 2}" y="${-c[1] - g.pad_h / 2}" width="${g.pad_w}" height="${g.pad_h}"/>`;
    }
    if (show('vDims')) body += dims(g, s);

    const fs = s * 0.026;
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${W.x} ${W.y} ${W.w} ${W.h}" preserveAspectRatio="xMidYMid meet">
<style>
.bg{fill:#0f3a25}
.cu{fill:#c27a3a;stroke:#7a4a1f;stroke-width:${lw};fill-rule:evenodd}
.cl{fill:none;stroke:#fff3c4;stroke-width:${lw * 1.2};stroke-dasharray:${lw * 8} ${lw * 5}}
.btm{fill:none;stroke:#4da3ff;stroke-opacity:.9;stroke-dasharray:${s / 60} ${s / 110};stroke-linecap:butt;stroke-linejoin:round}
.via{fill:#d6d6d6;stroke:#333;stroke-width:${lw}}
.drill{fill:#0f3a25}
.pad{fill:#e8c33a;stroke:#333;stroke-width:${lw}}
.board{fill:none;stroke:#dfe8e2;stroke-width:${lw * 2.4};stroke-dasharray:${s / 45} ${s / 70};opacity:.75}
.keep{fill:none;stroke:#8fb9a3;stroke-width:${lw * 1.4};stroke-dasharray:${s / 120} ${s / 120};opacity:.6}
.dim{stroke:#dfe8e2;stroke-width:${lw * 1.4};fill:none}
.dimt{fill:#dfe8e2;font:${fs}px system-ui,sans-serif;text-anchor:middle}
</style>
<rect class="bg" x="${W.x - W.w}" y="${W.y - W.h}" width="${W.w * 3}" height="${W.h * 3}"/>
${body}
</svg>`;
  }

  function dims(g, s) {
    const k = g.keepout;
    const x0 = k[0][0], x1 = k[1][0], yb = k[0][1], yt = k[2][1];
    const off = s * 0.045, tick = s * 0.012, fs = s * 0.026;
    const yT = -yt - off;                     // above the coil (screen coords)
    const xR = x1 + off;
    let out = `<path class="dim" d="M${x0} ${yT}H${x1}M${x0} ${yT - tick}v${2 * tick}M${x1} ${yT - tick}v${2 * tick}"/>`;
    out += `<text class="dimt" x="${(x0 + x1) / 2}" y="${yT - tick * 1.6}">${fmt(x1 - x0, 2)} mm</text>`;
    out += `<path class="dim" d="M${xR} ${-yt}V${-yb}M${xR - tick} ${-yt}h${2 * tick}M${xR - tick} ${-yb}h${2 * tick}"/>`;
    out += `<text class="dimt" transform="translate(${xR + tick * 1.6 + fs * 0.75} ${(-yt - yb) / 2}) rotate(90)">${fmt(yt - yb, 2)} mm</text>`;
    return out;
  }

  function drawLayout(g) {
    const W = worldBox(g);
    const key = [W.x, W.y, W.w, W.h].map(v => v.toFixed(2)).join(',');
    view.world = W;
    if (key !== view.worldKey || !view.box) { view.box = { ...W }; view.worldKey = key; }
    $('stage').innerHTML = layoutSvg(g, false);
    applyView();
  }

  function applyView() {
    const svg = $('stage').querySelector('svg');
    if (svg && view.box) svg.setAttribute('viewBox', `${view.box.x} ${view.box.y} ${view.box.w} ${view.box.h}`);
  }

  function toWorld(evt) {
    const svg = $('stage').querySelector('svg');
    if (!svg) return null;
    const pt = svg.createSVGPoint();
    pt.x = evt.clientX; pt.y = evt.clientY;
    const m = svg.getScreenCTM();
    if (!m) return null;
    const w = pt.matrixTransform(m.inverse());
    return { x: w.x, y: w.y };
  }

  function wireStage() {
    const st = $('stage');
    st.addEventListener('wheel', e => {
      if (!view.box) return;
      e.preventDefault();
      const w = toWorld(e);
      if (!w) return;
      const f = Math.exp(e.deltaY * 0.0015);
      const b = view.box;
      const nw = Math.min(Math.max(b.w * f, view.world.w / 60), view.world.w * 4);
      const r = nw / b.w;
      view.box = { x: w.x - (w.x - b.x) * r, y: w.y - (w.y - b.y) * r, w: b.w * r, h: b.h * r };
      applyView();
    }, { passive: false });
    let drag = null;
    st.addEventListener('pointerdown', e => {
      if (!view.box) return;
      drag = { x: e.clientX, y: e.clientY, box: { ...view.box } };
      st.setPointerCapture(e.pointerId);
      st.classList.add('drag');
    });
    st.addEventListener('pointermove', e => {
      const w = toWorld(e);
      if (w) $('cursor').textContent = `x ${w.x.toFixed(2)}  y ${(-w.y).toFixed(2)} mm`;
      if (!drag) return;
      const svg = st.querySelector('svg');
      const m = svg.getScreenCTM();
      if (!m) return;
      view.box = { ...drag.box, x: drag.box.x - (e.clientX - drag.x) / m.a, y: drag.box.y - (e.clientY - drag.y) / m.d };
      applyView();
    });
    const end = () => { drag = null; st.classList.remove('drag'); };
    st.addEventListener('pointerup', end);
    st.addEventListener('pointercancel', end);
    st.addEventListener('pointerleave', () => { $('cursor').textContent = ''; });
    st.addEventListener('dblclick', fit);
    $('fit').onclick = fit;
    for (const id of ['vCenter', 'vKeep', 'vBridge', 'vDims']) {
      $(id).addEventListener('change', () => last && drawLayout(last.geometry));
    }
  }
  function fit() { if (view.world) { view.box = { ...view.world }; applyView(); } }

  // ---------------------------------------------------------- details ----
  function drawDetails(o) {
    const r = o.results, d = o.derived, p = o.params;
    const rows = [
      ['sec', 'Inductance'],
      ['Selected model', `${fmt(r.L_uH, 4)} µH (${esc(r.model_used)})`],
      ['Greenhouse (segments)', `${fmt(r.L_greenhouse_uH, 4)} µH`],
      ['Mohan current sheet', `${fmt(r.L_mohan_uH, 4)} µH`],
      ['Ideal closed turns', `${fmt(r.L_ideal_closed_turns_uH, 4)} µH`],
      ['sec', 'Losses & tuning'],
      ['DC resistance', `${fmt(r.R_dc_ohm, 4)} Ω`],
      ['AC resistance', `${fmt(r.R_ac_ohm, 4)} Ω · skin depth ${fmt(r.skin_depth_um, 1)} µm`],
      ['Quality factor Q', fmt(r.Q, 1)],
      ['Resonating C (total)', `${fmt(r.C_total_resonant_pF, 3)} pF at ${fmt(r.f_MHz, 3)} MHz`],
    ];
    if (r.C_external_pF !== undefined && r.C_external_pF !== null) {
      rows.push(['External tuning C', `${fmt(r.C_external_pF, 3)} pF (chip ${fmt(r.C_chip_pF, 2)} pF)`]);
    }
    rows.push(
      ['sec', 'Geometry'],
      ['Conductor length', `${fmt(r.conductor_length_mm, 2)} mm in ${r.n_segments} segments`],
      ['Pitch', `${fmt(d.pitch, 3)} mm`],
      ['Radial build-up', `${fmt(d.build_up, 3)} mm per side`],
      ['Inner window', `${fmt(d.inner_x, 2)} × ${fmt(d.inner_y, 2)} mm`],
      ['Terminal x', `${fmt(d.term_x, 3)} mm (feasible ${fmt(d.term_x_min, 2)} … ${fmt(d.term_x_max, 2)})`],
      ['Corner radius range', `${fmt(d.corner_r_min, 2)} … ${fmt(d.corner_r_max, 2)} mm (rounds every turn)`],
      ['Measured clearance', `${fmt(r.min_clearance_mm, 4)} mm (requested ${fmt(p.gap, 3)} mm)`],
    );
    $('results').innerHTML = rows.map(([k, v]) => k === 'sec'
      ? `<tr class="sec"><td colspan="2">${v}</td></tr>`
      : `<tr><td>${k}</td><td>${v}</td></tr>`).join('');
  }

  // ------------------------------------------------------------ sweep ----
  async function loadSweep(force) {
    const p = readForm();
    if (!p) return null;
    const target = $('target_l').value;
    const { turns, ...rest } = p;
    const key = JSON.stringify([rest, target]);
    if (!force && key === sweepKey && sweepData) { drawSweep(sweepData, turns); return sweepData; }
    $('sweepChart').innerHTML = '<p class="muted">Sweeping turn counts…</p>';
    document.body.classList.add('busy');
    await new Promise(r => setTimeout(r, 30));      // let the message paint
    let out;
    try { out = await NFC.call('sweep', { params: p, target_uH: target }); }
    catch (e) { out = { ok: false, errors: ['error: ' + e.message] }; }
    finally { document.body.classList.remove('busy'); }
    if (!out.ok) { $('sweepChart').innerHTML = ''; showMessages(out.errors || []); return null; }
    sweepKey = key; sweepData = out;
    drawSweep(out, turns);
    return out;
  }

  function drawSweep(o, cur) {
    const rows = o.rows;
    const W = 720, H = 300, l = 52, rgt = 16, t = 14, b = 40;
    const nMax = rows[rows.length - 1].turns;
    const Ls = rows.map(r => r.L_uH);
    let yMax = Math.max(...Ls, o.target_uH || 0) * 1.08;
    const x = n => l + (nMax === 1 ? 0.5 : (n - 1) / (nMax - 1)) * (W - l - rgt);
    const y = v => t + (1 - v / yMax) * (H - t - b);
    const step = niceStep(yMax / 5);
    let grid = '';
    for (let v = 0; v <= yMax + 1e-9; v += step) {
      grid += `<line x1="${l}" x2="${W - rgt}" y1="${y(v)}" y2="${y(v)}" class="g"/><text x="${l - 6}" y="${y(v) + 4}" class="ax" text-anchor="end">${+v.toFixed(3)}</text>`;
    }
    const xStep = Math.max(1, Math.ceil(nMax / 12));
    for (let n = 1; n <= nMax; n += xStep) grid += `<text x="${x(n)}" y="${H - b + 16}" class="ax" text-anchor="middle">${n}</text>`;
    const line = rows.map((r, i) => (i ? 'L' : 'M') + x(r.turns) + ' ' + y(r.L_uH)).join('');
    const tgt = o.target_uH ? `<line x1="${l}" x2="${W - rgt}" y1="${y(o.target_uH)}" y2="${y(o.target_uH)}" class="tg"/><text x="${W - rgt}" y="${y(o.target_uH) - 5}" class="ax tgt" text-anchor="end">target ${o.target_uH} µH</text>` : '';
    const dots = rows.map(r => `<circle cx="${x(r.turns)}" cy="${y(r.L_uH)}" r="${r.turns === cur ? 6 : r.turns === o.best ? 5 : 3.2}" class="${r.turns === cur ? 'cur' : r.turns === o.best ? 'best' : 'pt'}" data-n="${r.turns}"><title>${r.turns} turns: ${fmt(r.L_uH, 3)} µH, Q ${fmt(r.Q, 0)}</title></circle>`).join('');
    $('sweepChart').innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Inductance against turn count">
<style>.g{stroke:var(--line);stroke-width:1}.ax{fill:var(--muted);font:11px system-ui,sans-serif}.ln{fill:none;stroke:var(--accent);stroke-width:2}
.pt{fill:var(--panel);stroke:var(--accent);stroke-width:1.6;cursor:pointer}.best{fill:var(--ok);stroke:var(--panel);stroke-width:1.5;cursor:pointer}
.cur{fill:var(--accent);stroke:var(--panel);stroke-width:2;cursor:pointer}.tg{stroke:var(--ok);stroke-dasharray:5 4;stroke-width:1.3}.tgt{fill:var(--ok)}</style>
${grid}<path d="${line}" class="ln"/>${tgt}${dots}
<text x="${(l + W - rgt) / 2}" y="${H - 6}" class="ax" text-anchor="middle">turns</text>
<text x="14" y="${(t + H - b) / 2}" class="ax" text-anchor="middle" transform="rotate(-90 14 ${(t + H - b) / 2})">L (µH)</text></svg>`;
    $('sweepChart').querySelectorAll('circle[data-n]').forEach(c => c.addEventListener('click', () => useTurns(+c.dataset.n)));

    $('sweepTable').innerHTML = '<thead><tr><th>N</th><th>L (µH)</th><th>R<sub>ac</sub> (Ω)</th><th>Q</th><th>C<sub>res</sub> (pF)</th><th>C<sub>ext</sub> (pF)</th></tr></thead><tbody>' +
      rows.map(r => `<tr class="${r.turns === o.best ? 'best' : ''} ${r.turns === cur ? 'cur' : ''}" data-n="${r.turns}"><td>${r.turns}</td><td>${fmt(r.L_uH, 3)}</td><td>${fmt(r.R_ac_ohm, 3)}</td><td>${fmt(r.Q, 1)}</td><td>${fmt(r.C_total_resonant_pF, 2)}</td><td>${fmt(r.C_external_pF, 2)}</td></tr>`).join('') + '</tbody>';
    $('sweepTable').querySelectorAll('tbody tr').forEach(tr => tr.addEventListener('click', () => useTurns(+tr.dataset.n)));
  }
  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const m = raw / p;
    return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
  }
  function useTurns(n) {
    $('f_turns').value = n;     // corner radius stays fixed, as in the sweep
    refresh();
  }

  // ---------------------------------------------------------- helpers ----
  async function autoCorner() {
    const p = readForm();
    if (!p) return;
    const out = await NFC.call('auto_corner', { params: p });
    if (!out.ok) { showMessages(out.errors || []); return; }
    $('f_shape').value = 'rrect';
    $('f_corner_r').value = out.corner_r;
    syncVisibility();
    refresh();
  }

  async function solve() {
    const tgt = Number($('target_l').value);
    if (!(tgt > 0)) { showMessages(['error: enter a target inductance in µH']); return; }
    const out = await loadSweep(true);
    if (!out) return;
    if (out.best === null) { showMessages(['error: no turn count fits this outline']); return; }
    const row = out.rows.find(r => r.turns === out.best);
    toast(`${out.best} turns → ${fmt(row.L_uH, 3)} µH (target ${tgt} µH)`);
    useTurns(out.best);
  }

  // -------------------------------------------------------- downloads ----
  function baseName() {
    const p = readForm() || {};
    return `nfc_${p.shape}_${p.length}x${p.width}_n${p.turns}`.replace(/[^\w.-]/g, '_');
  }

  async function saveText(text, filename, mime) {
    if (NFC.isDesktop()) {
      try {
        const path = await window.pywebview.api.save_file(filename, text, false);
        if (path) {
          toast(`Saved ${esc(path)} <a id="openFolder">Show folder</a>`, 6000);
          const a = $('openFolder');
          if (a) a.onclick = () => window.pywebview.api.open_folder(path);
        }
      } catch (e) { toast('Could not save: ' + esc(e.message || e)); }
      return;
    }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: mime || 'text/plain' }));
    a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }

  async function download(kind) {
    const p = readForm();
    if (!p) return;
    const name = baseName();
    const out = await NFC.call('export', { params: p, name });
    if (!out.ok) { showMessages(out.errors || []); return; }
    const map = { dxf: ['.dxf', 'image/vnd.dxf'], kicad_mod: ['.kicad_mod', 'text/plain'], json: ['.json', 'application/json'] };
    if (!out.files[kind]) { showMessages(out.errors || ['error: file not produced']); return; }
    await saveText(out.files[kind], out.name + map[kind][0], map[kind][1]);
  }

  // ---------------------------------------------------------- presets ----
  function applyParams(obj) {
    const p = obj && obj.parameters ? obj.parameters : obj;
    if (!p || typeof p !== 'object') throw new Error('no parameters in this file');
    setForm({ ...DEFAULTS, ...p });
    refresh();
  }

  async function loadPreset(name) {
    try {
      const r = await fetch(NFC.asset('examples/' + name + '.json'), { cache: 'no-cache' });
      if (!r.ok) throw new Error(r.status);
      applyParams(await r.json());
      toast('Loaded preset: ' + esc(name));
    } catch (e) { toast('Could not load preset ' + esc(name)); }
  }

  // ------------------------------------------------------------ share ----
  async function share() {
    const p = readForm();
    if (!p) return;
    const root = NFC.mode === 'browser' ? location.href.split('#')[0] : ONLINE;
    const url = root + '#d=' + b64e(JSON.stringify(diffFromDefaults(p)));
    try { await navigator.clipboard.writeText(url); toast('Link copied — it opens this design in the online app'); }
    catch (e) { prompt('Copy this link:', url); }
  }

  // ------------------------------------------------------------- tabs ----
  function wireTabs() {
    document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.toggle('on', x === t));
      document.querySelectorAll('.pane').forEach(p => { p.hidden = p.dataset.pane !== t.dataset.tab; });
      if (t.dataset.tab === 'sweep') loadSweep();
    }));
  }
  function openTab(name) { document.querySelector(`.tab[data-tab="${name}"]`).click(); }

  // ------------------------------------------------------------- start ----
  function setMode() {
    const label = { desktop: 'Desktop app', server: 'Local server', browser: 'Running in your browser' }[NFC.mode] || NFC.mode;
    const el = $('mode');
    el.textContent = label;
    el.className = 'mode ' + NFC.mode;
    $('engineNote').textContent = NFC.mode === 'browser'
      ? 'Python engine running locally via Pyodide — nothing is uploaded.'
      : 'Python engine: nfc_spiral.py on this computer.';
  }

  function wireTheme() {
    $('theme').onclick = () => {
      const cur = document.documentElement.dataset.theme ||
        (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem('nfc.theme', next); } catch (e) {}
    };
  }

  async function start() {
    wireTheme();
    try { await NFC.ready; } catch (e) { return; }
    const meta = await NFC.call('meta', {});
    DEFAULTS = meta.defaults;
    $('ver').textContent = 'v' + meta.version;
    setMode();

    buildForm();
    for (const [file, label] of EXAMPLES) $('preset').insertAdjacentHTML('beforeend', `<option value="${file}">${esc(label)}</option>`);
    setForm(initialParams());
    wireStage();
    wireTabs();

    $('preset').onchange = e => { if (e.target.value) loadPreset(e.target.value); e.target.value = ''; };
    $('loadJson').onclick = () => $('fileJson').click();
    $('fileJson').onchange = async e => {
      const f = e.target.files[0];
      if (!f) return;
      try { applyParams(JSON.parse(await f.text())); toast('Loaded ' + esc(f.name)); }
      catch (err) { toast('Could not read ' + esc(f.name) + ': ' + esc(err.message)); }
      e.target.value = '';
    };
    $('autoR').onclick = autoCorner;
    $('solve').onclick = () => { openTab('sweep'); solve(); };
    $('target_l').addEventListener('keydown', e => { if (e.key === 'Enter') { openTab('sweep'); solve(); } });
    $('dlDxf').onclick = () => download('dxf');
    $('dlKicad').onclick = () => download('kicad_mod');
    $('dlJson').onclick = () => download('json');
    $('dlSvg').onclick = () => last && saveText(layoutSvg(last.geometry, true), baseName() + '.svg', 'image/svg+xml');
    $('share').onclick = share;
    $('reset').onclick = () => { setForm({ ...DEFAULTS, ...START }); view.worldKey = ''; refresh(); };

    $('main').hidden = false;
    NFC.hideSplash();
    await refresh();
  }

  start();
})();
