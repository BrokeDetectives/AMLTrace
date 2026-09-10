/* AMLTrace - investigator console.
   No frameworks, no CDN: the force layout, the charts and the tables are all
   written here so the whole thing runs offline on a demo laptop. */

'use strict';

let S = null;                 // full application state from /api/state
let selectedAccount = null;
let dayFilter = null;

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const TIER_COLOR = { CRITICAL: '#ff4d6d', HIGH: '#ff9f43', MEDIUM: '#ffd93d', LOW: '#7f8fa6' };
const STAGE_COLOR = { PLACEMENT: '#4f8cff', LAYERING: '#c77dff', INTEGRATION: '#ff9f43' };

/* ------------------------------------------------------------- helpers */
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function inr(n) {                                   // Indian digit grouping
  n = Math.round(Number(n) || 0);
  const s = String(Math.abs(n));
  let out;
  if (s.length <= 3) out = s;
  else {
    const last3 = s.slice(-3), rest = s.slice(0, -3);
    out = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + last3;
  }
  return (n < 0 ? '-' : '') + '₹' + out;
}
function inrShort(n) {
  n = Number(n) || 0;
  if (Math.abs(n) >= 1e7) return '₹' + (n / 1e7).toFixed(2) + ' Cr';
  if (Math.abs(n) >= 1e5) return '₹' + (n / 1e5).toFixed(2) + ' L';
  return inr(n);
}
function toast(msg, ms = 2600) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('on');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('on'), ms);
}
function busy(on, msg) {
  $('#loading').classList.toggle('off', !on);
  if (msg) $('#loadmsg').textContent = msg;
}
async function api(url, opts) {
  const r = await fetch(url, opts);
  const j = await r.json().catch(() => ({ error: 'bad response' }));
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
const layerLabel = l => (S?.layer_meta?.[l]?.label) || l;
const layerRole  = l => (S?.layer_meta?.[l]?.role) || 'primary';
const alertOf    = a => S.alerts.find(x => x.account === a);

/* ------------------------------------------------------------------ SOUND */
// Synthesised, not sampled. The console is meant to run on a conference laptop
// with no network, and shipping four .mp3 files to make a click noise is not
// worth the download or the licence question. Everything below is a few
// oscillators through a gain envelope, which is nothing in file size.
//
// Short, but not so quiet it may as well not be there. Sound in an
// investigation tool earns its place by confirming that something happened - a
// re-run finished, a critical alert opened - and stops earning it the moment an
// analyst reaches for mute, which is why the toggle is in the sidebar.
const Snd = (() => {
  let ctx = null, master = null;
  let on = true;
  try { on = localStorage.getItem('amltrace.sound') !== 'off'; } catch (e) { /* no stored preference */ }

  // Sound is decoration. A browser with WebAudio disabled, a locked-down
  // machine, or an autoplay policy we did not anticipate must cost the analyst
  // a click noise and nothing else - every one of these calls sits in front of
  // a navigation handler, so an exception here would take the console with it.
  let broken = false;

  // Laptop speakers at a conference are the target, not headphones in an office.
  const MASTER = 0.5;
  // Everything is scheduled this far ahead of the clock. Scheduling at exactly
  // currentTime means the attack ramp is already in the past by the time the
  // graph is built, and the note comes out clipped or not at all.
  const LEAD = 0.02;

  function ready() {
    if (!on || broken) return false;
    try {
      if (!ctx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) { broken = true; return false; }  // no WebAudio: stay silent
        ctx = new AC();
        master = ctx.createGain();
        master.gain.value = MASTER;
        master.connect(ctx.destination);
      }
      return true;
    } catch (e) {
      broken = true;                               // do not try again this session
      return false;
    }
  }

  function emit({ freq, to, dur, type, gain }) {
    try {
      const t0 = ctx.currentTime + LEAD;
      const osc = ctx.createOscillator(), g = ctx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, t0);
      if (to) osc.frequency.exponentialRampToValueAtTime(Math.max(1, to), t0 + dur);
      // Linear attack from true silence, exponential decay. Ramping
      // exponentially *from* near-zero is the usual way to make a synth click,
      // because the curve spends most of the attack inaudible and then jumps.
      g.gain.setValueAtTime(0, t0);
      g.gain.linearRampToValueAtTime(gain, t0 + 0.010);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
      osc.connect(g); g.connect(master);
      osc.start(t0); osc.stop(t0 + dur + 0.03);
    } catch (e) { broken = true; }
  }

  function tone(spec) {
    if (!ready()) return;
    // A context created during a click starts suspended, and its clock does not
    // advance until resume() settles. Scheduling before that means every note
    // lands in the past and is silently dropped - which is exactly how the
    // first sound of a session goes missing while later ones work.
    if (ctx.state === 'suspended') {
      ctx.resume().then(() => emit(spec)).catch(() => { broken = true; });
    } else {
      emit(spec);
    }
  }

  function chord(specs) {
    specs.forEach((s, i) => setTimeout(() => tone(s), i * (s.after || 90)));
  }

  const api = {
    nav:    () => tone({ freq: 480, to: 660, dur: 0.11, type: 'triangle', gain: 0.55 }),
    select: () => tone({ freq: 760, dur: 0.09, type: 'sine', gain: 0.45 }),
    // Two rising notes: reads as "done" rather than "attention".
    done:   () => chord([{ freq: 523, dur: 0.14, type: 'sine', gain: 0.7, after: 110 },
                         { freq: 784, dur: 0.24, type: 'sine', gain: 0.7 }]),
    // A minor third downwards, twice. Deliberately not a klaxon: this fires when
    // an analyst opens a CRITICAL case, and it has to survive being heard often.
    alert:  () => chord([{ freq: 622, dur: 0.16, type: 'triangle', gain: 0.75, after: 140 },
                         { freq: 466, dur: 0.30, type: 'triangle', gain: 0.75 }]),
    // Falling sweep for a rejected file or a failed re-run.
    fail:   () => tone({ freq: 320, to: 120, dur: 0.34, type: 'sawtooth', gain: 0.5 }),
    // Rising sweep under a long pipeline run.
    scan:   () => tone({ freq: 200, to: 760, dur: 0.5, type: 'sine', gain: 0.4 }),
    enabled: () => on,
    // State for the "is the sound actually working" question, which is
    // otherwise unanswerable from outside the closure.
    diag: () => ({ on, broken, state: ctx ? ctx.state : 'no context',
                   master: master ? master.gain.value : null }),
    toggle() {
      on = !on;
      // Private windows and blocked site data make this throw, and losing the
      // preference between visits is a far smaller problem than a dead button.
      try { localStorage.setItem('amltrace.sound', on ? 'on' : 'off'); } catch (e) { /* ignore */ }
      if (on) { ready(); api.done(); }
      return on;
    },
  };
  return api;
})();

function wireSoundBtn() {
  const b = $('#soundBtn');
  if (!b) return;
  b.onclick = () => { Snd.toggle(); syncSoundBtn(); };
  syncSoundBtn();
}

function syncSoundBtn() {
  const b = $('#soundBtn');
  if (!b) return;
  const on = Snd.enabled();
  // The label states the CURRENT state and the tooltip states what a click will
  // do. Labelling it "Sound on" alone reads as an instruction rather than a
  // status, so the obvious way to test the sound - click the thing that says
  // sound - was silencing it instead.
  b.textContent = on ? '🔊 Sound on' : '🔇 Sound off';
  b.title = on ? 'Interface sounds are on. Click to mute.'
               : 'Interface sounds are muted. Click to turn them on.';
  b.classList.toggle('muted', !on);
  b.setAttribute('aria-pressed', String(on));
}

/* ------------------------------------------------------------ navigation */
$$('#nav button').forEach(b => b.onclick = () => { Snd.nav(); show(b.dataset.v); });
function show(v) {
  $$('#nav button').forEach(b => b.classList.toggle('on', b.dataset.v === v));
  $$('.view').forEach(s => s.classList.toggle('on', s.id === 'v-' + v));
  $('#main').scrollTop = 0;
  if (v === 'network') { sizeCanvas(); if (!net.nodes.length) buildNet(); reheat(); }
  if (v === 'timeline') drawTimeline();
  if (v === 'replay') { loadReplay().then(() => { drawReplayChart(); }); }
  if (v === 'economics') { if (!ECON) loadEconomics(); else renderEconomics(); }
  if (v === 'adversary') loadAdversary();
}

/* =========================================================== RENDER ALL */
function renderAll() {
  $('#tagAlerts').textContent = S.summary.flagged;
  $('#tagRings').textContent  = S.summary.rings;
  $('#tagWatch').textContent  = S.propagation.contamination.length;
  renderOverview(); renderAlerts(); renderRings(); renderPropagation();
  renderModel(); renderHardNegatives(); renderLab(); renderExports(); renderDossierPicker();
  renderHero(); renderPies(); renderProvenance(); renderWarnings();
  ECON = null;
  if ($('#v-economics').classList.contains('on')) loadEconomics();
  RP = null; $('#tagLatency').textContent = '-';
  if ($('#v-replay').classList.contains('on')) loadReplay();
  buildNet(); if ($('#v-network').classList.contains('on')) { sizeCanvas(); reheat(); }
  drawTimeline(); renderEpisodes(); renderEventStream();
}

/* -------------------------------------------------------------- OVERVIEW */
function renderWarnings() {
  const w = S.warnings || [];
  $('#warnBanner').innerHTML = w.length ? w.map(x => `
    <div class="card" style="border-color:var(--high);background:rgba(255,159,67,.07);margin-bottom:14px">
      <div style="display:flex;gap:11px;align-items:flex-start">
        <span style="color:var(--high);font-size:17px;line-height:1.2">&#9888;</span>
        <div><div style="font-weight:600;font-size:13.5px;color:var(--high);margin-bottom:3px">
          ${esc(x.layer)} - analysis was limited</div>
          <div style="font-size:12.5px;color:var(--muted);line-height:1.55">${esc(x.message)}</div></div>
      </div></div>`).join('') : '';
}

function renderOverview() {
  const s = S.summary, d = S.dataset, m = S.metrics;
  const kpis = [
    ['Accounts screened', d.accounts, `${d.transactions} transactions over ${d.days} days`, ''],
    ['Accounts alerted', s.flagged, `${(100 * s.flagged / d.accounts).toFixed(1)}% of the population`, 'high'],
    ['Critical + high', s.tiers.CRITICAL + s.tiers.HIGH, 'same-business-day escalation', 'crit'],
    ['Rings identified', s.rings, `modularity ${s.modularity}`, ''],
    ['Illicit value traced', inrShort(s.illicit_flow), `${(100 * s.illicit_share).toFixed(1)}% of all value moved`, 'med'],
    ['Exposure under alert', inrShort(s.exposure), 'total value touching alerted accounts', 'med'],
    ['New leads generated', s.watchlist, 'unflagged accounts exposed to alerts', ''],
    m.labelled
      ? ['Precision / recall', `${m.precision.toFixed(2)} / ${m.recall.toFixed(2)}`, `F1 ${m.f1.toFixed(2)} · ${m.fp} false positives`, 'ok']
      : ['Ground truth', 'not supplied', 'accuracy cannot be scored on this ledger', ''],
  ];
  $('#kpis').innerHTML = kpis.map(([k, v, n, c]) =>
    `<div class="kpi ${c}"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="n">${esc(n)}</div></div>`).join('');

  // layer contribution
  const lc = S.summary.layer_counts, max = Math.max(1, ...Object.values(lc));
  const order = ['STRUCTURING', 'FAN', 'CYCLE', 'PASS-THROUGH', 'ADAPTIVE', 'CENTRALITY', 'ML-ANOMALY'];
  $('#layerBars').innerHTML = order.filter(l => l in lc).map(l => {
    const role = layerRole(l);
    return `<div style="margin-bottom:11px">
      <div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px">
        <span>${esc(layerLabel(l))}
          <span class="chip ${role === 'primary' ? 'p' : 'c'}">${role}</span></span>
        <span class="mono">${lc[l]}</span></div>
      <div class="bar"><i style="width:${100 * lc[l] / max}%;${role === 'corroborating'
        ? 'background:linear-gradient(90deg,#4f8cff,#c77dff)' : ''}"></i></div></div>`;
  }).join('');

  // tier donut
  const t = S.summary.tiers, total = Object.values(t).reduce((a, b) => a + b, 0) || 1;
  let acc = 0, segs = '';
  for (const k of ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']) {
    if (!t[k]) continue;
    const frac = t[k] / total, r = 52, C = 2 * Math.PI * r;
    segs += `<circle cx="70" cy="70" r="${r}" fill="none" stroke="${TIER_COLOR[k]}" stroke-width="20"
      stroke-dasharray="${(frac * C).toFixed(1)} ${C}" stroke-dashoffset="${(-acc * C).toFixed(1)}"
      transform="rotate(-90 70 70)"><title>${k}: ${t[k]}</title></circle>`;
    acc += frac;
  }
  $('#tierChart').innerHTML = `<svg viewBox="0 0 140 140" style="width:140px;height:140px;display:block;margin:0 auto">
     ${segs}<text x="70" y="66" text-anchor="middle" fill="#e6edf7" font-size="24" font-family="monospace">${total}</text>
     <text x="70" y="83" text-anchor="middle" fill="#63769a" font-size="9" letter-spacing="1">ALERTS</text></svg>`;
  $('#tierRows').innerHTML = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map(k =>
    `<div style="display:flex;align-items:center;gap:9px;padding:4px 0;font-size:12.5px">
      <span class="pill ${k}">${k}</span><span class="faint" style="flex:1">${esc(tierAction(k))}</span>
      <b class="mono">${t[k]}</b></div>`).join('');

  // stage attribution
  const st = { PLACEMENT: 0, LAYERING: 0, INTEGRATION: 0 };
  S.timeline.events.forEach(e => st[e.stage] += e.amount);
  const stMax = Math.max(1, ...Object.values(st));
  $('#stageBars').innerHTML = Object.entries(st).map(([k, v]) =>
    `<div style="margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px">
        <span><span class="stagedot" style="display:inline-block;background:${STAGE_COLOR[k]}"></span> ${k}
          <span class="faint">${esc(S.timeline.stage_legend[k] || '')}</span></span>
        <span class="mono">${inrShort(v)}</span></div>
      <div class="bar"><i style="width:${100 * v / stMax}%;background:${STAGE_COLOR[k]}"></i></div></div>`).join('');

  // case narrative
  const top = S.alerts[0], ring = S.rings[0];
  const typologies = [...new Set(S.rings.map(r => r.typology))];
  $('#caseNarrative').innerHTML = `
    <p>Across a ledger of <b>${d.accounts}</b> accounts and <b>${d.transactions}</b> transactions worth
    ${inrShort(d.total_value)}, the pipeline raised <b>${s.flagged}</b> alerts organised into
    <b>${s.rings}</b> distinct rings covering ${typologies.length} typologies
    (${esc(typologies.join(', ').toLowerCase())}).</p>
    <p>The single highest-risk account is <b class="mono">${esc(top?.account || '—')}</b>
    (${esc(top?.risk_tier || '')}, corroborated by ${top?.layers.length || 0} independent layers),
    sitting in ${esc(top?.ring || 'no ring')} alongside
    ${ring ? ring.size - 1 : 0} other accounts moving ${inrShort(ring?.internal_value || 0)} between themselves.</p>
    <p>${inrShort(s.illicit_flow)} - <b>${(100 * s.illicit_share).toFixed(1)}%</b> of everything that moved,
    is attributable to laundering activity. A further <b>${s.watchlist}</b> accounts that no rule flagged
    are exposed to confirmed alerts and are queued for enhanced due diligence.</p>
    ${m.labelled ? `<p class="faint">Scored against ground truth: precision ${m.precision.toFixed(2)},
      recall ${m.recall.toFixed(2)}, F1 ${m.f1.toFixed(2)} (${m.tp} true positives, ${m.fp} false positives,
      ${m.fn} missed).</p>` : ''}`;
}
function tierAction(k) {
  return { CRITICAL: 'Freeze review + STR within 7 days', HIGH: 'Same-business-day escalation',
           MEDIUM: 'Analyst review queue, T+1', LOW: 'Monitor only' }[k];
}

/* ---------------------------------------------------------------- ALERTS */
let alertSort = { key: 'risk_score', dir: -1 };
function renderAlerts() {
  const sel = $('#alertLayer');
  const layers = [...new Set(S.alerts.flatMap(a => a.layers))].sort();
  sel.innerHTML = '<option value="">All layers</option>' +
    layers.map(l => `<option value="${esc(l)}">${esc(layerLabel(l))}</option>`).join('');
  drawAlertRows();
}
function drawAlertRows() {
  const q = $('#alertSearch').value.trim().toLowerCase();
  const tier = $('#alertTier').value, layer = $('#alertLayer').value;
  let rows = S.alerts.filter(a =>
    (!tier || a.risk_tier === tier) && (!layer || a.layers.includes(layer)) &&
    (!q || (a.account + ' ' + a.typology + ' ' + (a.ring || '') + ' ' + a.layers.join(' ')).toLowerCase().includes(q)));
  const { key, dir } = alertSort;
  rows = rows.slice().sort((x, y) => {
    const a = x[key], b = y[key];
    return (typeof a === 'number' ? a - b : String(a).localeCompare(String(b))) * dir;
  });
  $('#alertCount').textContent = `${rows.length} of ${S.alerts.length} alerts`;
  $('#alertTable tbody').innerHTML = rows.length ? rows.map(a => `
    <tr data-a="${esc(a.account)}" class="${a.account === selectedAccount ? 'sel' : ''}">
      <td class="acct">${esc(a.account)}</td>
      <td><span class="pill ${a.risk_tier}">${a.risk_tier}</span> <span class="mono faint">${a.risk_score}</span></td>
      <td>${esc(a.typology)}</td>
      <td class="mono faint">${esc(a.ring || '—')}</td>
      <td class="num">${inrShort(a.exposure)}</td>
      <td class="num">${a.txn_count}</td>
      <td class="mono faint">${esc(a.sla)}</td></tr>`).join('')
    : '<tr><td colspan="7" class="empty">No alerts match this filter.</td></tr>';
  $$('#alertTable tbody tr[data-a]').forEach(tr => tr.onclick = () => {
    const a = alertOf(tr.dataset.a);
    if (a && a.risk_tier === 'CRITICAL') Snd.alert(); else Snd.select();
    selectAccount(tr.dataset.a);
  });
}
$('#alertSearch').oninput = drawAlertRows;
$('#alertTier').onchange = drawAlertRows;
$('#alertLayer').onchange = drawAlertRows;
$$('#alertTable th').forEach(th => th.onclick = () => {
  const k = th.dataset.s;
  alertSort = { key: k, dir: alertSort.key === k ? -alertSort.dir : -1 };
  drawAlertRows();
});

function selectAccount(acc) {
  selectedAccount = acc;
  drawAlertRows();
  const a = alertOf(acc);
  if (!a) return;
  $('#alertDetail').innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <h3 style="margin:0;font-size:18px;text-transform:none;color:var(--txt)" class="mono">${esc(a.account)}</h3>
      <span class="pill ${a.risk_tier}">${a.risk_tier}</span>
      <span class="faint mono">score ${a.risk_score}</span></div>
    <p class="sub" style="margin-top:6px">${esc(a.action)} · SLA ${esc(a.sla)}</p>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0">
      ${[['Inflow', inrShort(a.inflow)], ['Outflow', inrShort(a.outflow)],
         ['Transactions', a.txn_count], ['Ring', a.ring || '—'],
         ['Sub-network betweenness', a.centrality], ['ML anomaly score', a.ml_score ?? '—']]
        .map(([k, v]) => `<div style="background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 10px">
          <div class="faint" style="font-size:10.5px;text-transform:uppercase;letter-spacing:.6px">${esc(k)}</div>
          <div class="mono" style="font-size:13.5px;margin-top:2px">${esc(v)}</div></div>`).join('')}
    </div>
    <h3>Grounds of suspicion</h3>
    <div style="max-height:300px;overflow:auto;margin-top:8px">
      ${a.reasons.map(r => `<div style="border-left:2px solid ${layerRole(r.layer) === 'primary' ? 'var(--accent)' : 'var(--accent2)'};
          padding:2px 0 2px 11px;margin-bottom:12px">
        <div><span class="chip ${layerRole(r.layer) === 'primary' ? 'p' : 'c'}">${esc(layerLabel(r.layer))}</span></div>
        <div style="font-size:12.5px;margin-top:5px;color:var(--muted)">${esc(r.detail)}</div>
        ${r.evidence?.length ? `<div style="margin-top:5px">${r.evidence.slice(0, 8).map(e =>
          `<span class="chip">${esc(e)}</span>`).join('')}${r.evidence.length > 8
            ? `<span class="faint">+${r.evidence.length - 8}</span>` : ''}</div>` : ''}
      </div>`).join('')}
    </div>
    <div class="toolbar" style="margin:12px 0 0">
      <button class="btn primary" onclick="openDossier('${esc(a.account)}')">▤ Generate STR</button>
      <button class="btn" onclick="focusOnNetwork('${esc(a.account)}')">⬡ Show in network</button>
    </div>`;
}
window.selectAccount = selectAccount;

/* ----------------------------------------------------------------- RINGS */
function renderRings() {
  $('#ringCards').innerHTML = S.rings.map(r => {
    const members = r.members.map(m => {
      const al = alertOf(m);
      return `<span class="chip" style="cursor:pointer;color:${TIER_COLOR[al?.risk_tier] || 'var(--muted)'}"
        onclick="selectAccount('${esc(m)}');show('alerts')">${esc(m)}</span>`;
    }).join('');
    return `<div class="card">
      <div style="display:flex;align-items:center;gap:9px">
        <h3 style="margin:0;color:var(--accent)">${esc(r.ring_id)}</h3>
        <span class="pill ${r.peak_risk >= 4 ? 'CRITICAL' : r.peak_risk >= 3 ? 'HIGH' : 'MEDIUM'}">${esc(r.typology)}</span>
      </div>
      <div style="display:flex;gap:18px;margin:11px 0;font-size:12.5px" class="muted">
        <span>accounts <b class="mono" style="color:var(--txt)">${r.size}</b></span>
        <span>internal value <b class="mono" style="color:var(--txt)">${inrShort(r.internal_value)}</b></span>
        <span>peak risk <b class="mono" style="color:var(--txt)">${r.peak_risk}</b></span></div>
      <div>${r.layers.map(l => `<span class="chip ${layerRole(l) === 'primary' ? 'p' : 'c'}">${esc(layerLabel(l))}</span>`).join('')}</div>
      <div style="margin-top:9px">${members}</div>
      <button class="btn" style="margin-top:11px" onclick="focusRing('${esc(r.ring_id)}')">⬡ Isolate in network</button>
    </div>`;
  }).join('') || '<div class="empty">No rings detected.</div>';
}
window.focusRing = id => {
  const r = S.rings.find(x => x.ring_id === id);
  if (!r) return;
  net.focus = new Set(r.members);
  net.focusLabel = id;
  show('network'); reheat();
};

/* ----------------------------------------------------------- PROPAGATION */
function renderPropagation() {
  const rows = S.propagation.contamination;
  $('#watchTable tbody').innerHTML = rows.length ? rows.map(r => `
    <tr>
      <td class="acct">${esc(r.account)}</td>
      <td style="min-width:110px"><div class="bar"><i style="width:${(r.score * 100).toFixed(0)}%"></i></div>
        <span class="mono faint" style="font-size:11px">${r.score.toFixed(3)}</span></td>
      <td class="num">${r.hops}</td>
      <td><span class="chip p" style="cursor:pointer" onclick="selectAccount('${esc(r.nearest_alert)}');show('alerts')">${esc(r.nearest_alert)}</span></td>
      <td style="font-size:11.5px" class="mono muted">${r.path.map(esc).join(' → ')}</td>
      <td style="font-size:12px" class="muted">${esc(r.verdict)}</td>
    </tr>`).join('') : '<tr><td colspan="6" class="empty">No unflagged accounts are exposed to the alert set.</td></tr>';

  const pr = S.propagation.pagerank, max = pr[0]?.pagerank || 1;
  const contamSet = new Set(rows.map(r => r.account));
  $('#prList').innerHTML = pr.map(p => `
    <div style="display:flex;align-items:center;gap:9px;padding:6px 0;border-bottom:1px solid var(--line)">
      <span class="acct" style="width:66px">${esc(p.account)}</span>
      <div class="bar" style="flex:1"><i style="width:${100 * p.pagerank / max}%"></i></div>
      <span class="mono faint" style="font-size:11px;width:60px;text-align:right">${p.pagerank.toFixed(5)}</span>
      ${contamSet.has(p.account) ? '<span class="chip p" title="Also on the contamination watchlist">✓</span>'
        : '<span class="chip" style="opacity:.3">–</span>'}
    </div>`).join('') || '<div class="empty">No propagation seeds.</div>';
}

/* -------------------------------------------------------------- TIMELINE */
function drawTimeline() {
  const svg = $('#tlChart');
  const series = S.timeline.series;
  const W = svg.clientWidth || 900, H = 250, P = { t: 14, r: 46, b: 26, l: 62 };
  const iw = W - P.l - P.r, ih = H - P.t - P.b;
  const maxV = Math.max(1, ...series.map(d => d.PLACEMENT + d.LAYERING + d.INTEGRATION));
  const maxC = Math.max(1, ...series.map(d => d.cumulative));
  const bw = Math.max(2, iw / series.length - 2);
  const x = i => P.l + (i + .5) * (iw / series.length);
  const y = v => P.t + ih - (v / maxV) * ih;

  let g = `<g>`;
  for (let i = 0; i <= 4; i++) {
    const yy = P.t + ih - (i / 4) * ih;
    g += `<line x1="${P.l}" x2="${W - P.r}" y1="${yy}" y2="${yy}" stroke="#243149" stroke-width="1"/>
          <text x="${P.l - 8}" y="${yy + 4}" text-anchor="end" fill="#63769a" font-size="10" font-family="monospace">${inrShort(maxV * i / 4)}</text>`;
  }
  series.forEach((d, i) => {
    let base = P.t + ih;
    const hit = dayFilter === d.day;
    ['PLACEMENT', 'LAYERING', 'INTEGRATION'].forEach(k => {
      if (!d[k]) return;
      const h = (d[k] / maxV) * ih;
      base -= h;
      g += `<rect x="${x(i) - bw / 2}" y="${base}" width="${bw}" height="${h}" fill="${STAGE_COLOR[k]}"
             opacity="${dayFilter && !hit ? .28 : .9}" rx="1"><title>Day ${d.day} (${d.date}) - ${k}: ${inr(d[k])}</title></rect>`;
    });
    g += `<rect class="tlhit" data-day="${d.day}" x="${x(i) - (iw / series.length) / 2}" y="${P.t}"
           width="${iw / series.length}" height="${ih}" fill="transparent" style="cursor:pointer"><title>Day ${d.day} - ${d.count} illicit transactions</title></rect>`;
    if (d.day % 5 === 0 || i === 0)
      g += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle" fill="#63769a" font-size="10" font-family="monospace">${d.day}</text>`;
  });
  const line = series.map((d, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${(P.t + ih - (d.cumulative / maxC) * ih).toFixed(1)}`).join(' ');
  g += `<path d="${line}" fill="none" stroke="#8ea0bd" stroke-width="1.6" stroke-dasharray="3 3"/>`;
  for (let i = 0; i <= 2; i++) {
    const yy = P.t + ih - (i / 2) * ih;
    g += `<text x="${W - P.r + 8}" y="${yy + 4}" fill="#63769a" font-size="10" font-family="monospace">${inrShort(maxC * i / 2)}</text>`;
  }
  g += `<text x="${W - P.r + 8}" y="${P.t - 3}" fill="#63769a" font-size="9">cumulative</text></g>`;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.innerHTML = g;
  $$('.tlhit', svg).forEach(r => r.onclick = () => {
    dayFilter = dayFilter === +r.dataset.day ? null : +r.dataset.day;
    drawTimeline(); renderEventStream();
  });
}
window.addEventListener('resize', () => {
  if ($('#v-timeline').classList.contains('on')) drawTimeline();
  if ($('#v-replay').classList.contains('on')) drawReplayChart();
  sizeCanvas();
});

function renderEpisodes() {
  $('#evRing').innerHTML = '<option value="">All rings</option>' +
    S.timeline.episodes.map(e => `<option>${esc(e.ring_id)}</option>`).join('');
  $('#episodes').innerHTML = S.timeline.episodes.map(e => {
    const tot = Object.values(e.stage_breakdown).reduce((a, b) => a + b, 0) || 1;
    const track = ['PLACEMENT', 'LAYERING', 'INTEGRATION'].map(k =>
      e.stage_breakdown[k] ? `<i style="width:${100 * e.stage_breakdown[k] / tot}%;background:${STAGE_COLOR[k]}"></i>` : '').join('');
    return `<div class="ep">
      <div class="h"><b>${esc(e.ring_id)}</b><span class="pill MEDIUM">${esc(e.typology)}</span>
        <span class="faint mono" style="font-size:11.5px">${esc(e.first_date)} → ${esc(e.last_date)}</span></div>
      <div class="m">
        <span>value <b>${inrShort(e.total_value)}</b></span>
        <span>duration <b>${e.duration_days}d</b></span>
        <span>hops <b>${e.hops}</b></span>
        <span>velocity <b>${inrShort(e.velocity)}/day</b></span>
        <span>max rest <b>${e.max_resting_days}d</b></span></div>
      <div class="track">${track}</div>
      <div style="margin-top:9px">${e.members.slice(0, 9).map(m =>
        `<span class="chip" style="cursor:pointer" onclick="selectAccount('${esc(m)}');show('alerts')">${esc(m)}</span>`).join('')}</div>
      <button class="btn" style="margin-top:9px" onclick="$('#evRing').value='${esc(e.ring_id)}';renderEventStream()">
        ⧗ Follow the money</button>
    </div>`;
  }).join('') || '<div class="empty">No episodes reconstructed.</div>';
}
$('#evRing').onchange = renderEventStream;
$('#evStage').onchange = renderEventStream;
$('#evReset').onclick = () => { $('#evRing').value = ''; $('#evStage').value = ''; dayFilter = null; drawTimeline(); renderEventStream(); };

function renderEventStream() {
  const ring = $('#evRing').value, stage = $('#evStage').value;
  const ev = S.timeline.events.filter(e =>
    (!ring || e.ring === ring) && (!stage || e.stage === stage) && (!dayFilter || e.day === dayFilter));
  $('#evFilterLabel').textContent = dayFilter ? `· day ${dayFilter}` : '';
  $('#eventStream').innerHTML = ev.length ? ev.map(e => `
    <div class="flowrow">
      <div class="d">D${String(e.day).padStart(2, '0')}<br>${esc(e.timestamp.slice(11, 16))}</div>
      <div class="b">
        <span class="stagedot" style="background:${STAGE_COLOR[e.stage]}" title="${e.stage}"></span>
        <span class="acct" style="cursor:pointer" onclick="selectAccount('${esc(e.sender)}');show('alerts')">${esc(e.sender)}</span>
        <span class="faint">→</span>
        <span class="acct" style="cursor:pointer" onclick="selectAccount('${esc(e.receiver)}');show('alerts')">${esc(e.receiver)}</span>
        <span class="mono" style="color:var(--accent)">${inrShort(e.amount)}</span>
        <span class="chip">${esc(e.channel)}</span>
        <span class="chip" style="color:${STAGE_COLOR[e.stage]}">${esc(e.stage)}</span>
        <span class="chip">${esc(e.ring)}</span>
      </div></div>`).join('')
    : '<div class="empty">No events for this filter.</div>';
}

/* ----------------------------------------------------------------- MODEL */
/* --------------------------------------------------- ADVERSARIAL TEST */
// Cached for the life of the page: the benchmark scores four whole pipelines
// and none of it depends on the ledger currently loaded, so re-running it every
// time the analyst opens the tab would cost seconds and change nothing.
let ADV = null;

const ADV_TESTS = [
  ['Rhythm',
   'Coefficient of variation of the gaps between hops. Trade between two businesses is irregular; '
   + 'money being walked along a relay arrives on a schedule, because someone is operating it. '
   + 'Evading a time window means committing to a rhythm, and the rhythm is the tell.'],
  ['Conservation',
   'One sum surviving several hops with its size intact, distinct from the traffic those accounts '
   + 'otherwise carry. Laundering has to move a particular amount from A to Z; ordinary payments '
   + 'between the same parties are unrelated in size.'],
  ['Net exposure',
   'Cover traffic is cheap: hand a mule reciprocal round-trips with friendly accounts and its gross '
   + 'volume rises until any dedication ratio falls below threshold. Netting each counterparty pair '
   + 'first removes the disguise, because money that comes straight back changes nothing.'],
];

async function loadAdversary() {
  if (ADV) { renderAdversary(); return; }
  $('#advRows').innerHTML = '<div class="faint">Scoring both adversarial ledgers…</div>';
  try {
    const r = await fetch('/api/evasion');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    ADV = (await r.json()).scenarios || [];
  } catch (e) {
    $('#advRows').innerHTML = '<div class="faint">Could not run the benchmark: ' + esc(String(e.message || e)) + '</div>';
    return;
  }
  renderAdversary();
}

function renderAdversary() {
  if (!ADV) return;
  const saved = ADV.reduce((n, s) => n + (s.caught_with - s.caught_without), 0);
  $('#tagAdv').textContent = '+' + saved;
  $('#advRows').innerHTML = ADV.map(s => {
    const off = Math.round(100 * s.recall_without_adaptive);
    const on  = Math.round(100 * s.recall_with_adaptive);
    return `<div class="advrow">
      <div class="advhead">
        <span>${esc(s.scenario)}</span>
        <span class="mono faint">${s.criminal_accounts} accounts placed</span>
      </div>
      <p class="sub">${esc(s.note)}</p>
      <div class="advbars">
        <div>
          <div class="advlbl">Layers 1-8 only<span class="mono">${off}% recall</span></div>
          <div class="bar"><i class="bad" style="width:${off}%"></i></div>
          <div class="faint" style="font-size:12px;margin-top:3px">caught ${s.caught_without} of ${s.criminal_accounts}</div>
        </div>
        <div>
          <div class="advlbl">With the adaptive layer<span class="mono">${on}% recall</span></div>
          <div class="bar"><i class="good" style="width:${on}%"></i></div>
          <div class="faint" style="font-size:12px;margin-top:3px">caught ${s.caught_with} of ${s.criminal_accounts},
            ${s.false_positives_with} false positive${s.false_positives_with === 1 ? '' : 's'}</div>
        </div>
      </div>
    </div>`;
  }).join('');
  $('#advExplain').innerHTML = ADV_TESTS.map(([t, d], i) =>
    `<div class="advtest"><div class="advtest-h"><span class="advnum">D${i + 1}</span>${esc(t)}</div>
     <p>${esc(d)}</p></div>`).join('')
    + '<p class="sub" style="margin-top:12px">Two of the three must agree, and every hop on the relay '
    + 'must be one-shot, before an account is flagged. A standing settlement corridor between the same '
    + 'two parties is rhythmic and conserves value, which is why the one-shot test is what keeps an '
    + 'acquiring bank out of the alert queue.</p>';
}

function renderModel() {
  const m = S.metrics;
  if (!m.labelled) {
    $('#metricNote').textContent = 'This ledger has no is_fraud labels, so accuracy cannot be scored.';
    $('#metricTiles').innerHTML = ''; $('#confusion').innerHTML = ''; $('#errorList').innerHTML = '';
  } else {
    $('#metricNote').textContent =
      `${S.dataset.fraud_linked_accounts} of ${S.dataset.accounts} accounts are genuinely fraud-linked.`;
    $('#metricTiles').innerHTML = [['Precision', m.precision], ['Recall', m.recall], ['F1', m.f1]]
      .map(([k, v]) => `<div class="kpi ok"><div class="k">${k}</div><div class="v">${v.toFixed(2)}</div></div>`).join('');
    $('#confusion').innerHTML = `
      <div class="tp"><div class="n">${m.tp}</div><div class="l">True positive</div></div>
      <div class="fp"><div class="n">${m.fp}</div><div class="l">False positive</div></div>
      <div class="fn"><div class="n">${m.fn}</div><div class="l">False negative</div></div>
      <div class="tn"><div class="n">${m.tn}</div><div class="l">True negative</div></div>`;
    $('#errorList').innerHTML =
      (m.false_positives.length ? `<div><span class="chip c">False positives</span> ${m.false_positives.map(a => `<span class="chip">${esc(a)}</span>`).join('')}</div>` : '') +
      (m.false_negatives.length ? `<div style="margin-top:6px"><span class="chip c">Missed</span> ${m.false_negatives.map(a => `<span class="chip">${esc(a)}</span>`).join('')}</div>` : '') ||
      '<div class="faint">No classification errors on this ledger.</div>';
  }

  const ab = S.ablation;
  $('#ablation').innerHTML = ab.length ? `<table><thead><tr>
      <th style="cursor:default">Configuration</th><th class="num" style="cursor:default">P</th>
      <th class="num" style="cursor:default">R</th><th class="num" style="cursor:default">F1</th>
      <th class="num" style="cursor:default">FP</th><th style="cursor:default;width:120px">Recall</th></tr></thead><tbody>
    ${ab.map((r, i) => `<tr${i === ab.length - 1 ? ' style="opacity:.85"' : ''}>
      <td>${i === ab.length - 1 ? '<span class="chip c">baseline</span> ' : ''}${esc(r.name)}</td>
      <td class="num">${r.precision.toFixed(2)}</td><td class="num">${r.recall.toFixed(2)}</td>
      <td class="num"><b>${r.f1.toFixed(2)}</b></td>
      <td class="num" style="color:${r.fp ? 'var(--high)' : 'var(--muted)'}">${r.fp}</td>
      <td><div class="bar"><i style="width:${r.recall * 100}%;${i === ab.length - 1 ? 'background:var(--high)' : ''}"></i></div></td>
    </tr>`).join('')}</tbody></table>
    <p class="sub" style="margin-top:12px">Read the last row against the one above it: unsupervised anomaly
      detection with no graph context recovers a fraction of the network at a fraction of the precision.
      Relational structure - who pays whom, in what shape, in what order - is where the signal lives.</p>`
    : '<div class="empty">Ablation needs a labelled ledger.</div>';

  const rb = S.robustness;
  $('#robustness').innerHTML = rb.perturbations.length ? `
    <div style="display:flex;align-items:baseline;gap:12px;margin-bottom:10px">
      <span class="mono" style="font-size:26px;color:var(--ok)">${rb.mean_jaccard}</span>
      <span class="muted">mean Jaccard similarity across ${rb.perturbations.length} independent threshold perturbations</span></div>
    <table><thead><tr><th style="cursor:default">Perturbation</th><th class="num" style="cursor:default">Alerts</th>
      <th class="num" style="cursor:default">Jaccard</th><th style="cursor:default;width:200px"></th></tr></thead><tbody>
    ${rb.perturbations.map(p => `<tr><td>${esc(p.name)}</td><td class="num">${p.flagged}</td>
      <td class="num">${p.jaccard.toFixed(2)}</td>
      <td><div class="bar"><i style="width:${p.jaccard * 100}%;background:${p.jaccard > .9 ? 'var(--ok)' : p.jaccard > .7 ? 'var(--med)' : 'var(--crit)'}"></i></div></td></tr>`).join('')}
    </tbody></table>` : '<div class="empty">—</div>';
}

/* ------------------------------------------------------------------- LAB */
const SLIDERS = [
  ['structuring_floor_pct', 'Structuring band floor', 'Lower edge of the "just under CTR" window', .5, .99, .01],
  ['structuring_min_count', 'Structuring min transfers', 'How many near-threshold transfers before it is a pattern', 2, 10, 1],
  ['structuring_window_days', 'Structuring window (days)', 'Transfers must cluster inside this many days', 1, 15, 1],
  ['fan_min_branches', 'Fan min branches', 'Intermediaries required for a split-and-rejoin', 2, 8, 1],
  ['cycle_max_len', 'Max cycle length', 'Longest loop the detector will chase', 3, 9, 1],
  ['cycle_max_span_slack', 'Cycle closing slack (days)', 'How much slower than one-hop-per-day a real loop may be', 0, 15, 1],
  ['pt_ratio_low', 'Pass-through min ratio', 'Least a mule may forward of what it received', .5, 1, .01],
  ['pt_window_days', 'Pass-through hold (days)', 'Longest a conduit may hold funds', 0, 10, 1],
  ['pt_min_amount', 'Pass-through min amount', 'Ignore conduit hops below this value', 50000, 1000000, 50000],
  ['centrality_z', 'Centrality z-threshold', 'Standard deviations above peer alerts to count as a chokepoint', .5, 4, .1],
  ['ml_cutoff', 'ML anomaly cutoff', 'IsolationForest score below which an account is anomalous', -.3, .1, .01],
  ['prop_decay', 'Propagation decay/hop', 'How fast suspicion fades with distance', .1, .9, .05],
  ['prop_max_hops', 'Propagation max hops', 'How far suspicion may travel', 1, 5, 1],
  ['adaptive_rhythm_cv', 'Evasion rhythm tolerance', 'How irregular relay hops may be before they read as traded, not scheduled', .05, 1, .05],
  ['adaptive_net_dedication', 'Evasion net dedication', 'Share of an account left after cover traffic is netted out', .2, 1, .05],
  ['adaptive_min_conservation', 'Evasion value conservation', 'How much of the original sum must survive the relay', .1, 1, .05],
  ['adaptive_agg_hug', 'Inferred line hug', 'How close tranches must sit to the reporting line the engine infers', .4, 1, .05],
];
const LAYER_TOGGLES = [['use_structuring', 'Structuring'], ['use_fan', 'Fan patterns'], ['use_cycles', 'Cycles'],
  ['use_pass_through', 'Pass-through'], ['use_adaptive', 'Threshold evasion'],
  ['use_centrality', 'Centrality'], ['use_ml', 'ML anomaly']];

function renderLab() {
  const c = S.config;
  $('#sliders').innerHTML = SLIDERS.map(([k, lbl, note, mn, mx, st]) => `
    <div class="slider-row">
      <div class="lbl">${esc(lbl)}<small>${esc(note)}</small></div>
      <input type="range" id="sl-${k}" min="${mn}" max="${mx}" step="${st}" value="${c[k]}">
      <div class="val" id="vl-${k}">${fmtCfg(k, c[k])}</div>
    </div>`).join('');
  SLIDERS.forEach(([k]) => $('#sl-' + k).oninput = e => $('#vl-' + k).textContent = fmtCfg(k, e.target.value));
  $('#layerToggles').innerHTML = LAYER_TOGGLES.map(([k, l]) =>
    `<label class="sw"><input type="checkbox" id="tg-${k}" ${c[k] ? 'checked' : ''}> ${esc(l)}</label>`).join('');
  renderLabResult();
}
function fmtCfg(k, v) {
  v = Number(v);
  if (k === 'pt_min_amount') return inrShort(v);
  if (k === 'structuring_floor_pct') return (v * 100).toFixed(0) + '% of CTR';
  if (k.startsWith('adaptive_')) return (v * 100).toFixed(0) + '%';
  return Number.isInteger(v) ? String(v) : v.toFixed(2);
}
function readCfg() {
  const o = {};
  SLIDERS.forEach(([k]) => o[k] = Number($('#sl-' + k).value));
  LAYER_TOGGLES.forEach(([k]) => o[k] = $('#tg-' + k).checked);
  return o;
}
function renderLabResult() {
  const m = S.metrics, s = S.summary;
  $('#labResult').innerHTML = `
    <div class="grid kpis" style="grid-template-columns:1fr 1fr">
      <div class="kpi"><div class="k">Alerts</div><div class="v">${s.flagged}</div></div>
      <div class="kpi"><div class="k">Rings</div><div class="v">${s.rings}</div></div>
      ${m.labelled ? `<div class="kpi ok"><div class="k">Precision</div><div class="v">${m.precision.toFixed(2)}</div>
        <div class="n">${m.fp} false positives</div></div>
      <div class="kpi ok"><div class="k">Recall</div><div class="v">${m.recall.toFixed(2)}</div>
        <div class="n">${m.fn} missed</div></div>` : ''}
    </div>`;
}
$('#applyCfg').onclick = async () => {
  Snd.scan();
  busy(true, 'Re-running all nine detection layers…');
  try { S = await api('/api/reconfigure', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: readCfg(), ablation: true }) });
    renderAll(); $('#cfgStatus').textContent = 'Re-run complete.'; toast('Pipeline re-run with new thresholds');
    Snd.done();
  } catch (e) { Snd.fail(); toast('Failed: ' + e.message); } finally { busy(false); }
};
$('#resetCfg').onclick = async () => {
  busy(true, 'Restoring defaults…');
  try { S = await api('/api/simulate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ seed: Number($('#seed').value) || 7 }) });
    renderAll(); toast('Defaults restored'); Snd.done();
  } catch (e) { Snd.fail(); toast('Failed: ' + e.message); } finally { busy(false); }
};
$('#regen').onclick = async () => {
  Snd.scan();
  busy(true, 'Generating a new ledger and analysing it…');
  try {
    S = await api('/api/simulate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seed: Number($('#seed').value) || 7, n_normal: Number($('#nnorm').value) || 135,
        n_normal_txn: Number($('#ntxn').value) || 150, config: readCfg() }) });
    renderAll(); toast(`New ledger: ${S.dataset.accounts} accounts, ${S.dataset.transactions} transactions`);
    Snd.done();
  } catch (e) { Snd.fail(); toast('Failed: ' + e.message); } finally { busy(false); }
};
$('#sweepBtn').onclick = async () => {
  const seeds = [7, 21, 42, 101, 2026], out = [];
  const keep = JSON.parse(JSON.stringify(S));
  busy(true, 'Running the same pipeline over five independent ledgers…');
  try {
    for (const sd of seeds) {
      const r = await api('/api/simulate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ seed: sd, n_normal: Number($('#nnorm').value) || 135,
          n_normal_txn: Number($('#ntxn').value) || 150 }) });
      out.push({ seed: sd, ...r.metrics, rings: r.summary.rings, flagged: r.summary.flagged });
      $('#loadmsg').textContent = `Seed ${sd} - precision ${r.metrics.precision.toFixed(2)}, recall ${r.metrics.recall.toFixed(2)}`;
    }
    const avg = k => (out.reduce((a, b) => a + b[k], 0) / out.length).toFixed(3);
    $('#seedSweep').innerHTML = `<table><thead><tr><th style="cursor:default">Seed</th>
      <th class="num" style="cursor:default">Alerts</th><th class="num" style="cursor:default">Rings</th>
      <th class="num" style="cursor:default">P</th><th class="num" style="cursor:default">R</th>
      <th class="num" style="cursor:default">F1</th></tr></thead><tbody>
      ${out.map(o => `<tr><td class="mono">${o.seed}</td><td class="num">${o.flagged}</td><td class="num">${o.rings}</td>
        <td class="num">${o.precision.toFixed(2)}</td><td class="num">${o.recall.toFixed(2)}</td>
        <td class="num"><b>${o.f1.toFixed(2)}</b></td></tr>`).join('')}
      <tr style="background:var(--panel2)"><td><b>mean</b></td><td class="num">—</td><td class="num">—</td>
        <td class="num"><b>${avg('precision')}</b></td><td class="num"><b>${avg('recall')}</b></td>
        <td class="num"><b>${avg('f1')}</b></td></tr></tbody></table>`;
    // restore the ledger the analyst was looking at
    S = await api('/api/simulate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seed: keep.seed ?? 7 }) });
    renderAll();
    toast('Sweep complete - mean F1 ' + avg('f1'));
  } catch (e) { toast('Sweep failed: ' + e.message); } finally { busy(false); }
};

// CSV upload
const drop = $('#drop'), fileInput = $('#file');
drop.onclick = () => fileInput.click();
drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
drop.ondragleave = () => drop.classList.remove('over');
drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); };
fileInput.onchange = () => fileInput.files[0] && upload(fileInput.files[0]);
async function upload(f) {
  busy(true, 'Analysing ' + f.name + '…');
  try {
    const fd = new FormData(); fd.append('file', f);
    S = await api('/api/upload', { method: 'POST', body: fd });
    renderAll();
    $('#uploadMsg').innerHTML = `<span class="chip p">loaded</span> ${esc(f.name)} - ${S.dataset.accounts} accounts,
      ${S.dataset.transactions} transactions, ${S.summary.flagged} alerted${S.dataset.labelled
      ? `, precision ${S.metrics.precision.toFixed(2)}` : ' (no labels supplied)'}`;
    toast('Ledger analysed'); show('overview');
  } catch (e) {
    $('#uploadMsg').innerHTML = `<span class="chip" style="color:var(--crit)">error</span> ${esc(e.message)}`;
  } finally { busy(false); }
}

/* --------------------------------------------------------------- EXPORTS */
function renderExports() {
  const items = [
    ['Alert queue', 'Every alerted account with its tier, exposure and full grounds of suspicion.', '/api/export/alerts.csv', 'CSV'],
    ['FIU-IND STR bundle', 'One filing-ready row per CRITICAL/HIGH account, with narrative and regulatory basis.', '/api/export/str_bundle.csv', 'CSV'],
    ['Laundering timeline', 'Every illicit transaction with its stage attribution and ring.', '/api/export/timeline.csv', 'CSV'],
    ['Propagation watchlist', 'Unflagged accounts exposed to alerts, with the path back to known-bad.', '/api/export/watchlist.csv', 'CSV'],
    ['Detected rings', 'Community structure derived from the alert sub-network.', '/api/export/rings.csv', 'CSV'],
    ['Full ledger', 'The transaction data the analysis ran on.', '/api/export/transactions.csv', 'CSV'],
    ['Streaming replay', 'Day-by-day alert population, precision and recall as the ledger arrives.', '/api/export/replay.csv', 'CSV'],
    ['Detection latency', 'How many days each account went undetected after it started transacting.', '/api/export/latency.csv', 'CSV'],
    ['Hard negatives', 'Lawful look-alike families and whether the detector correctly ignored them.', '/api/export/decoys.csv', 'CSV'],
    ['Model evidence', 'Metrics, ablation, discriminators, hard negatives, robustness, economics, config.', '/api/export/metrics.json', 'JSON'],
    ['Data provenance', 'Every calibrated parameter with its published source and confidence level.', '/api/export/provenance.json', 'JSON'],
  ];
  $('#exportCards').innerHTML = items.map(([t, d, u, k]) => `
    <div class="card"><h3>${esc(t)}</h3><p class="sub">${esc(d)}</p>
      <a class="btn primary" href="${u}">⤓ Download ${k}</a></div>`).join('');
}

/* --------------------------------------------------------------- DOSSIER */
function renderDossierPicker() {
  const pick = $('#dossierPick');
  const eligible = S.alerts.filter(a => a.risk_tier === 'CRITICAL' || a.risk_tier === 'HIGH');
  const list = (eligible.length ? eligible : S.alerts);
  pick.innerHTML = list.map(a => `<option value="${esc(a.account)}">${esc(a.account)} - ${a.risk_tier}</option>`).join('');
  pick.onchange = () => openDossier(pick.value, true);
  if (list.length) openDossier(list[0].account, true);
  else $('#dossierBody').innerHTML = '<div class="empty">No alerts to report.</div>';
}
window.openDossier = async (acc, stay) => {
  if (!stay) show('dossier');
  $('#dossierPick').value = acc;
  $('#dossierJson').href = `/api/export/str/${encodeURIComponent(acc)}.json`;
  $('#dossierBody').innerHTML = '<div class="empty">Generating…</div>';
  try {
    const d = await api('/api/dossier/' + encodeURIComponent(acc));
    $('#dossierBody').innerHTML = dossierHtml(d);
  } catch (e) { $('#dossierBody').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
};
$('#dossierPrint').onclick = () => window.print();

function dossierHtml(d) {
  const t = d.transaction_summary;
  const zRows = Object.entries(d.behavioural_z_scores)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 6);
  return `<div class="dossier">
    <h1>${esc(d.report_type)}</h1>
    <div class="ref">${esc(d.report_reference)} &nbsp;·&nbsp; ${esc(d.date_of_report)} &nbsp;·&nbsp; ${esc(d.reporting_entity)}</div>
    <hr>
    <h4>Part A - Subject account</h4>
    <dl class="kv">
      <dt>Account identifier</dt><dd>${esc(d.account_id)}</dd>
      <dt>Risk classification</dt><dd>${esc(d.risk_tier)} (composite score ${d.risk_score})</dd>
      <dt>Recommended action</dt><dd>${esc(d.recommended_action)}</dd>
      <dt>Action taken</dt><dd>${esc(d.action_taken)}</dd>
      <dt>Associated ring</dt><dd>${d.associated_ring
        ? esc(d.associated_ring.ring_id) + ' - ' + esc(d.associated_ring.typology) +
          ' (' + d.associated_ring.members.length + ' accounts, ' + inr(d.associated_ring.internal_value) + ' internal)'
        : 'None - standalone subject'}</dd>
    </dl>

    <h4>Part B - Grounds of suspicion</h4>
    <div class="narr">${esc(d.narrative)}</div>
    <ol>${d.grounds_of_suspicion.map(g => `<li>
      <span class="lay ${g.role === 'corroborating' ? 'corr' : ''}">${esc(g.layer_label)}</span>
      ${esc(g.detail)}
      ${g.evidence?.length ? `<div style="font:11px monospace;color:#5a6270;margin-top:4px">Evidence: ${g.evidence.slice(0, 12).map(esc).join(', ')}</div>` : ''}
    </li>`).join('')}</ol>

    <h4>Part C - Transaction profile</h4>
    <dl class="kv">
      <dt>Total credits</dt><dd>${inr(t.total_inflow)}</dd>
      <dt>Total debits</dt><dd>${inr(t.total_outflow)}</dd>
      <dt>Net position</dt><dd>${inr(t.net_position)}</dd>
      <dt>Transactions</dt><dd>${t.transaction_count} across ${t.counterparties} counterparties</dd>
      <dt>Activity window</dt><dd>day ${t.first_activity_day} to day ${t.last_activity_day}</dd>
      <dt>Credits just below CTR</dt><dd>${t.credits_just_below_ctr} (threshold ${inr(d.ctr_threshold_applied)})</dd>
      <dt>Sub-network betweenness</dt><dd>${d.betweenness_centrality}</dd>
      <dt>ML anomaly score</dt><dd>${d.ml_anomaly_score ?? 'n/a'}</dd>
    </dl>
    <table style="margin-top:10px"><thead><tr><th>Txn</th><th>Date</th><th>Dr/Cr</th><th>Counterparty</th>
      <th style="text-align:right">Amount</th><th>Channel</th></tr></thead><tbody>
      ${d.transactions.map(x => `<tr><td style="font-family:monospace">${esc(x.txn_id)}</td><td>${esc(x.date)}</td>
        <td>${esc(x.direction)}</td><td style="font-family:monospace">${esc(x.counterparty)}</td>
        <td style="text-align:right;font-family:monospace">${inr(x.amount)}</td><td>${esc(x.channel)}</td></tr>`).join('')}
    </tbody></table>

    <h4>Part D - Behavioural deviation</h4>
    <table><thead><tr><th>Feature</th><th style="text-align:right">z-score vs population</th><th>Reading</th></tr></thead><tbody>
      ${zRows.map(([k, v]) => `<tr><td style="font-family:monospace">${esc(k)}</td>
        <td style="text-align:right;font-family:monospace">${v >= 0 ? '+' : ''}${v.toFixed(2)}</td>
        <td>${Math.abs(v) > 2 ? 'Materially abnormal' : Math.abs(v) > 1 ? 'Elevated' : 'Within normal range'}</td></tr>`).join('')}
    </tbody></table>

    <h4>Part E - Immediate network</h4>
    <p style="font-size:13px">${d.one_hop_subgraph.nodes.length} accounts, ${d.one_hop_subgraph.edges.length} directed
      relationships. Counterparties already under alert are marked.</p>
    <table><thead><tr><th>From</th><th>To</th><th style="text-align:right">Aggregate value</th><th style="text-align:right">Txns</th></tr></thead><tbody>
      ${d.one_hop_subgraph.edges.map(e => `<tr><td style="font-family:monospace">${esc(e.source)}</td>
        <td style="font-family:monospace">${esc(e.target)}</td>
        <td style="text-align:right;font-family:monospace">${inr(e.amount)}</td>
        <td style="text-align:right">${e.count}</td></tr>`).join('')}
    </tbody></table>

    <h4>Part F - Regulatory basis</h4>
    <p style="font-size:13px">${esc(d.regulatory_basis)}</p>
    <div class="sig"><div>Principal Officer - signature &amp; date</div><div>Designated Director - countersignature</div></div>
  </div>`;
}

/* --------------------------------------------------------------- NETWORK */
const cv = $('#net'), ctx = cv.getContext('2d');
const net = { nodes: [], edges: [], byId: new Map(), focus: null, focusLabel: '',
              tx: 0, ty: 0, k: 1, alpha: 0, drag: null, hover: null, raf: 0,
              // 3D: yaw/pitch orbit, perspective divide, stage-plane mode
              is3d: false, yaw: 0.55, pitch: -0.38, spin: false, stages: false, fov: 900 };

const STAGE_Z = { PLACEMENT: -250, LAYERING: 0, INTEGRATION: 250 };

/* Which laundering stage does this account mostly act in? Used as the depth
   axis in stage-plane mode, so the z-position carries real meaning rather
   than being decoration: money enters at the back and exits at the front. */
function stageOf(id) {
  let best = null, bestV = -1;
  const tally = {};
  for (const e of S.timeline.events) {
    if (e.sender === id || e.receiver === id) tally[e.stage] = (tally[e.stage] || 0) + e.amount;
  }
  for (const k in tally) if (tally[k] > bestV) { bestV = tally[k]; best = k; }
  return best;
}

/* Rotate a world point by yaw then pitch, then apply the perspective divide.
   Returns screen offsets plus the scale factor, which everything downstream
   uses for depth cueing (size, alpha, line width). */
function project(n) {
  if (!net.is3d) return { x: n.x, y: n.y, s: 1, depth: 0 };
  const cy = Math.cos(net.yaw), sy = Math.sin(net.yaw);
  const cp = Math.cos(net.pitch), sp = Math.sin(net.pitch);
  const x1 = n.x * cy - n.z * sy;
  const z1 = n.x * sy + n.z * cy;
  const y2 = n.y * cp - z1 * sp;
  const z2 = n.y * sp + z1 * cp;
  const s = net.fov / (net.fov + z2);
  return { x: x1 * s, y: y2 * s, s, depth: z2 };
}

function sizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const r = cv.getBoundingClientRect();
  if (!r.width) return;
  cv.width = r.width * dpr; cv.height = r.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  net.w = r.width; net.h = r.height;
}
function buildNet() {
  const mode = $('#netFilter').value;
  const alerted = new Set(S.alerts.map(a => a.account));
  let keep;
  if (mode === 'alerts') keep = new Set(alerted);
  else if (mode === 'all') keep = new Set(S.graph.nodes.map(n => n.id));
  else {
    keep = new Set(alerted);
    S.graph.edges.forEach(e => {
      if (alerted.has(e.source)) keep.add(e.target);
      if (alerted.has(e.target)) keep.add(e.source);
    });
  }
  if (net.focus) {
    const f = new Set(net.focus);
    S.graph.edges.forEach(e => { if (net.focus.has(e.source)) f.add(e.target); if (net.focus.has(e.target)) f.add(e.source); });
    keep = f;
  }
  const maxVol = Math.max(1, ...S.graph.nodes.map(n => n.volume));
  net.nodes = S.graph.nodes.filter(n => keep.has(n.id)).map(n => ({
    ...n, x: (Math.random() - .5) * 480, y: (Math.random() - .5) * 380,
    z: (Math.random() - .5) * 320, vx: 0, vy: 0, vz: 0,
    stage: stageOf(n.id),
    r: 4 + 9 * Math.sqrt(n.volume / maxVol) + (n.risk ? 2 : 0), pinned: false,
  }));
  // give every detected ring its own anchor on a circle, so rings separate
  // visually instead of collapsing into one hairball
  const ringIds = [...new Set(net.nodes.map(n => n.ring).filter(Boolean))];
  net.anchors = new Map();
  const R = 120 + 26 * ringIds.length;
  ringIds.forEach((rid, i) => {
    const ang = (i / ringIds.length) * Math.PI * 2 - Math.PI / 2;
    // in 3D the rings sit on a sphere-ish shell rather than a flat circle
    const tilt = ((i % 3) - 1) * 0.55;
    net.anchors.set(rid, { x: Math.cos(ang) * R, y: Math.sin(ang) * R * .72,
                           z: Math.sin(ang * 1.7 + tilt) * R * .55 });
  });
  net.nodes.forEach(n => {
    const a = n.ring && net.anchors.get(n.ring);
    if (a) {
      n.x = a.x + (Math.random() - .5) * 70;
      n.y = a.y + (Math.random() - .5) * 70;
      n.z = a.z + (Math.random() - .5) * 60;
    }
  });
  net.byId = new Map(net.nodes.map(n => [n.id, n]));
  net.edges = S.graph.edges.filter(e => net.byId.has(e.source) && net.byId.has(e.target))
    .map(e => ({ ...e, s: net.byId.get(e.source), t: net.byId.get(e.target) }));
  net.maxEdge = Math.max(1, ...net.edges.map(e => e.amount));
  $('#netStat').textContent = `${net.nodes.length} accounts · ${net.edges.length} relationships` +
    (net.focus ? ` · focus ${net.focusLabel}` : '');
  net.tx = 0; net.ty = 0; net.k = 1; net.fitted = 0;
  reheat();
}
function reheat() { net.alpha = 1; net.fitted = 0; if (!net.raf) tick(); }

function tick() {
  net.raf = requestAnimationFrame(tick);
  if (net.spin && net.is3d) { net.yaw += 0.0032; }
  if (net.alpha > 0.002) {
    // fit twice: once as the layout takes shape, again once it has settled,
    // because nodes are still travelling to their stage planes on the first pass
    if (net.alpha < 0.30 && !net.fitted) { net.fitted = 1; setTimeout(fitNet, 30); }
    else if (net.alpha < 0.045 && net.fitted === 1) { net.fitted = 2; setTimeout(fitNet, 30); }
    net.alpha *= 0.985;
    const N = net.nodes, n = N.length;
    const rep = 1600 * net.alpha + 520;
    for (let i = 0; i < n; i++) {
      const a = N[i];
      const anc = a.ring && net.anchors && net.anchors.get(a.ring);
      if (anc) {                                            // pull toward own ring
        a.vx += (anc.x - a.x) * 0.030; a.vy += (anc.y - a.y) * 0.030;
        if (net.is3d && !net.stages) a.vz += (anc.z - a.z) * 0.030;
      } else {
        a.vx -= a.x * 0.0011; a.vy -= a.y * 0.0011;
        if (net.is3d && !net.stages) a.vz -= a.z * 0.0011;
      }
      if (net.is3d && net.stages) {         // depth axis becomes the laundering stage
        const target = a.stage ? STAGE_Z[a.stage] : 0;
        a.vz += (target - a.z) * 0.075;
      }
      if (!net.is3d) a.vz += (0 - a.z) * 0.10;              // collapse to a plane in 2D
      for (let j = i + 1; j < n; j++) {
        const b = N[j];
        let dx = b.x - a.x, dy = b.y - a.y, dz = net.is3d ? b.z - a.z : 0;
        let d2 = dx * dx + dy * dy + dz * dz;
        if (d2 < 1e-4) { dx = Math.random() - .5; dy = Math.random() - .5; dz = Math.random() - .5; d2 = .25; }
        if (d2 > 160000) continue;                          // ignore distant pairs
        const f = rep / d2, d = Math.sqrt(d2);
        const fx = f * dx / d, fy = f * dy / d, fz = f * dz / d;
        a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy;
        if (net.is3d) { a.vz -= fz; b.vz += fz; }
      }
    }
    for (const e of net.edges) {                            // springs
      const dx = e.t.x - e.s.x, dy = e.t.y - e.s.y, dz = net.is3d ? e.t.z - e.s.z : 0;
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1, want = 74;
      const f = (d - want) * 0.016;
      const fx = f * dx / d, fy = f * dy / d, fz = f * dz / d;
      e.s.vx += fx; e.s.vy += fy; e.t.vx -= fx; e.t.vy -= fy;
      if (net.is3d && !net.stages) { e.s.vz += fz; e.t.vz -= fz; }
    }
    for (const a of N) {
      if (a.pinned || net.drag === a) { a.vx = a.vy = a.vz = 0; continue; }
      a.vx *= 0.82; a.vy *= 0.82; a.vz *= 0.82;
      a.x += Math.max(-18, Math.min(18, a.vx));
      a.y += Math.max(-18, Math.min(18, a.vy));
      a.z += Math.max(-18, Math.min(18, a.vz));
    }
  }
  draw();
}
function draw() {
  if (!net.w) return;
  ctx.clearRect(0, 0, net.w, net.h);
  ctx.save();
  ctx.translate(net.w / 2 + net.tx, net.h / 2 + net.ty);
  ctx.scale(net.k, net.k);
  const showLabels = $('#netLabels').checked, showAmt = $('#netAmounts').checked;
  const hl = net.hover ? new Set([net.hover.id]) : null;
  if (net.hover) net.edges.forEach(e => { if (e.s === net.hover) hl.add(e.t.id); if (e.t === net.hover) hl.add(e.s.id); });

  // project once per frame, then painter's algorithm: far things first
  for (const a of net.nodes) a._p = project(a);
  const order = net.is3d ? net.nodes.slice().sort((a, b) => b._p.depth - a._p.depth) : net.nodes;

  if (net.is3d && net.stages) drawStagePlanes();

  for (const e of net.edges) {                              // edges
    const dim = hl && !(hl.has(e.s.id) && hl.has(e.t.id));
    ctx.strokeStyle = e.illicit ? (dim ? 'rgba(255,120,90,.16)' : 'rgba(255,140,110,.62)')
                                : (dim ? 'rgba(120,140,175,.07)' : 'rgba(130,150,185,.24)');
    ctx.lineWidth = (0.5 + 2.2 * Math.sqrt(e.amount / net.maxEdge)) / net.k;
    const ps = e.s._p, pt = e.t._p;
    const dx = pt.x - ps.x, dy = pt.y - ps.y, d = Math.hypot(dx, dy) || 1;
    const ux = dx / d, uy = dy / d;
    const x1 = ps.x + ux * e.s.r * ps.s, y1 = ps.y + uy * e.s.r * ps.s;
    const x2 = pt.x - ux * (e.t.r * pt.s + 4), y2 = pt.y - uy * (e.t.r * pt.s + 4);
    const mx = (x1 + x2) / 2 - uy * d * .11, my = (y1 + y2) / 2 + ux * d * .11;
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.quadraticCurveTo(mx, my, x2, y2); ctx.stroke();
    if (!dim && net.k > .55) {                              // arrowhead
      const ax = x2 - (x2 - mx) / Math.hypot(x2 - mx, y2 - my) * 0, ay = y2;
      const ang = Math.atan2(y2 - my, x2 - mx), s = 6 / net.k;
      ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath();
      ctx.moveTo(x2, y2);
      ctx.lineTo(x2 - s * Math.cos(ang - .4), y2 - s * Math.sin(ang - .4));
      ctx.lineTo(x2 - s * Math.cos(ang + .4), y2 - s * Math.sin(ang + .4));
      ctx.closePath(); ctx.fill();
    }
    if (showAmt && !dim && net.k > .8 && e.amount > 400000) {
      ctx.fillStyle = 'rgba(190,205,230,.75)';
      ctx.font = `${9 / net.k}px ui-monospace,monospace`;
      ctx.textAlign = 'center';
      ctx.fillText(inrShort(e.amount), mx, my - 3 / net.k);
    }
  }
  for (const a of order) {                                 // nodes
    const dim = hl && !hl.has(a.id);
    const col = a.risk ? TIER_COLOR[a.tier] : '#3a4a63';
    const p = a._p, rr = a.r * p.s;
    // depth cue: distant nodes fade toward the background
    const fog = net.is3d ? Math.max(.16, Math.min(1, p.s * p.s * 1.25)) : 1;
    ctx.globalAlpha = (dim ? .18 : 1) * fog;
    if (a.risk >= 3 && !dim) {
      const g = ctx.createRadialGradient(p.x, p.y, rr * .4, p.x, p.y, rr + 10 / net.k);
      g.addColorStop(0, col + '55'); g.addColorStop(1, col + '00');
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(p.x, p.y, rr + 10 / net.k, 0, 7); ctx.fill();
    }
    ctx.beginPath(); ctx.arc(p.x, p.y, rr, 0, 7);
    ctx.fillStyle = col; ctx.fill();
    ctx.lineWidth = 1.2 / net.k;
    ctx.strokeStyle = a === net.hover ? '#fff' : 'rgba(4,6,13,.85)';
    ctx.stroke();
    if (a.pinned) { ctx.beginPath(); ctx.arc(p.x, p.y, rr + 3 / net.k, 0, 7);
      ctx.strokeStyle = '#38e0d8'; ctx.lineWidth = 1.4 / net.k; ctx.stroke(); }
    if (showLabels && !dim && (net.k > .7 || a.risk) && (!net.is3d || p.s > .78)) {
      ctx.fillStyle = a.risk ? '#e8eefb' : '#93a6c8';
      ctx.font = `${(a.risk ? 600 : 400)} ${10 * (net.is3d ? p.s : 1) / net.k}px ui-monospace,monospace`;
      ctx.textAlign = 'center';
      ctx.fillText(a.id, p.x, p.y - rr - 4 / net.k);
    }
    ctx.globalAlpha = 1;
  }
  ctx.restore();
}
/* The three depth planes of the laundering cycle, drawn as receding grids so
   the z-axis is legible as a claim rather than an effect. */
function drawStagePlanes() {
  const order = Object.entries(STAGE_Z).sort((a, b) => b[1] - a[1]);
  for (const [stage, z] of order) {
    const col = { PLACEMENT: '77,124,255', LAYERING: '179,136,255', INTEGRATION: '255,159,67' }[stage];
    const S2 = 300, step = 75;
    // faint tinted quad so the plane reads as a surface, not just lines
    const c1 = project({ x: -S2, y: -S2, z }), c2 = project({ x: S2, y: -S2, z });
    const c3 = project({ x: S2, y: S2, z }), c4 = project({ x: -S2, y: S2, z });
    ctx.beginPath();
    ctx.moveTo(c1.x, c1.y); ctx.lineTo(c2.x, c2.y); ctx.lineTo(c3.x, c3.y); ctx.lineTo(c4.x, c4.y);
    ctx.closePath();
    ctx.fillStyle = `rgba(${col},.045)`; ctx.fill();
    ctx.strokeStyle = `rgba(${col},.26)`;
    ctx.lineWidth = 1.1 / net.k;
    ctx.beginPath();
    for (let i = -S2; i <= S2; i += step) {
      let p = project({ x: i, y: -S2, z }); ctx.moveTo(p.x, p.y);
      p = project({ x: i, y: S2, z }); ctx.lineTo(p.x, p.y);
      p = project({ x: -S2, y: i, z }); ctx.moveTo(p.x, p.y);
      p = project({ x: S2, y: i, z }); ctx.lineTo(p.x, p.y);
    }
    ctx.stroke();
    const lp = project({ x: -S2 + 8, y: S2 + 26, z });
    ctx.font = `700 ${14 / net.k}px ui-monospace,monospace`;
    ctx.textAlign = 'left';
    const label = stage + '  ──';
    ctx.lineWidth = 3 / net.k;
    ctx.strokeStyle = 'rgba(4,6,13,.9)';
    ctx.strokeText(label, lp.x, lp.y);
    ctx.fillStyle = `rgba(${col},1)`;
    ctx.fillText(label, lp.x, lp.y);
  }
}

function toWorld(px, py) {
  return { x: (px - net.w / 2 - net.tx) / net.k, y: (py - net.h / 2 - net.ty) / net.k };
}
function pick(px, py) {
  const p = toWorld(px, py);
  let best = null, bd = 1e9;
  for (const a of net.nodes) {
    const q = a._p || project(a);
    const d = Math.hypot(q.x - p.x, q.y - p.y);
    // nearest hit wins, but prefer the one closest to the camera on a tie
    if (d < a.r * q.s + 6 && d - q.depth * 0.02 < bd) { bd = d - q.depth * 0.02; best = a; }
  }
  return best;
}
let panning = null;
let orbiting = null;
cv.onmousedown = e => {
  const r = cv.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
  const n = pick(px, py);
  if (n) { net.drag = n; n.pinned = true; return; }
  // in 3D an empty-space drag orbits the camera; shift-drag still pans
  if (net.is3d && !e.shiftKey) {
    orbiting = { x: e.clientX, y: e.clientY, yaw: net.yaw, pitch: net.pitch };
    cv.classList.add('drag');
  } else {
    panning = { x: e.clientX - net.tx, y: e.clientY - net.ty };
    cv.classList.add('drag');
  }
};
cv.onmousemove = e => {
  const r = cv.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
  if (net.drag) {
    const p = toWorld(px, py);
    const sc = net.is3d ? (net.drag._p ? net.drag._p.s : 1) : 1;
    net.drag.x = p.x / sc; net.drag.y = p.y / sc; reheat(); return;
  }
  if (orbiting) {
    net.yaw = orbiting.yaw + (e.clientX - orbiting.x) * 0.006;
    net.pitch = Math.max(-1.35, Math.min(1.35, orbiting.pitch + (e.clientY - orbiting.y) * 0.005));
    draw(); return;
  }
  if (panning) { net.tx = e.clientX - panning.x; net.ty = e.clientY - panning.y; draw(); return; }
  const n = pick(px, py);
  if (n !== net.hover) { net.hover = n; draw(); }
  const tip = $('#netTip');
  if (n) {
    const a = alertOf(n.id);
    tip.style.display = 'block';
    tip.style.left = Math.min(px + 16, r.width - 300) + 'px';
    tip.style.top = (py + 16) + 'px';
    tip.innerHTML = `<b>${esc(n.id)}</b> ${a ? `<span class="pill ${a.risk_tier}">${a.risk_tier}</span>` : '<span class="faint">not alerted</span>'}
      <div style="margin-top:5px;color:#8ea0bd">value moved ${inrShort(n.volume)}${n.ring ? ' · ' + esc(n.ring) : ''}</div>
      ${a ? `<div style="margin-top:4px">${a.layers.map(l => `<span class="chip p">${esc(layerLabel(l))}</span>`).join('')}</div>` : ''}
      <div class="faint" style="margin-top:5px;font-size:11px">click to isolate · drag to pin</div>`;
  } else tip.style.display = 'none';
};
window.addEventListener('mouseup', () => {
  if (net.drag) { net.drag = null; }
  panning = null; orbiting = null; cv.classList.remove('drag');
});
cv.onclick = e => {
  const r = cv.getBoundingClientRect();
  const n = pick(e.clientX - r.left, e.clientY - r.top);
  if (!n) return;
  if (alertOf(n.id)) selectAccount(n.id);
  net.focus = new Set([n.id]); net.focusLabel = n.id;
  buildNet();
};
cv.onwheel = e => {
  e.preventDefault();
  const r = cv.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
  const before = toWorld(px, py);
  net.k = Math.max(.18, Math.min(4.5, net.k * (e.deltaY < 0 ? 1.13 : 1 / 1.13)));
  const after = toWorld(px, py);
  net.tx += (after.x - before.x) * net.k; net.ty += (after.y - before.y) * net.k;
  draw();
};
function sync3dUi() {
  $('#net3d').textContent = net.is3d ? '◈ 3D on' : '◈ 3D';
  $('#net3d').style.color = net.is3d ? 'var(--accent)' : '';
  $('#netStages').style.display = net.is3d ? '' : 'none';
  $('#netSpin').style.display = net.is3d ? '' : 'none';
  $('#netStages').style.color = net.stages ? 'var(--accent)' : '';
  $('#netSpin').style.color = net.spin ? 'var(--accent)' : '';
  $('#legend3d').textContent = net.is3d
    ? (net.stages ? 'Depth = laundering stage · drag to orbit'
                  : 'Depth = ring separation · drag to orbit')
    : '';
}
$('#net3d').onclick = () => {
  net.is3d = !net.is3d;
  if (!net.is3d) { net.stages = false; net.spin = false; }
  sync3dUi(); net.fitted = 0; reheat();
};
$('#netStages').onclick = () => {
  net.stages = !net.stages;
  // a camera angle that actually separates the three planes instead of
  // stacking them on top of each other
  if (net.stages) { net.yaw = 1.02; net.pitch = -0.22; net.spin = false; }
  sync3dUi(); net.fitted = 0; reheat();
};
$('#netSpin').onclick = () => { net.spin = !net.spin; sync3dUi(); if (!net.raf) tick(); };
$('#netFilter').onchange = () => { net.focus = null; buildNet(); };
$('#netReheat').onclick = () => { net.nodes.forEach(n => { n.pinned = false; n.x = (Math.random() - .5) * 480; n.y = (Math.random() - .5) * 380; }); reheat(); };
$('#netClear').onclick = () => { net.focus = null; net.focusLabel = ''; buildNet(); };
$('#netLabels').onchange = draw;
$('#netAmounts').onchange = draw;
function fitNet() {
  if (!net.nodes.length || !net.w) return;
  for (const a of net.nodes) a._p = project(a);
  const xs = net.nodes.map(n => n._p.x), ys = net.nodes.map(n => n._p.y);
  if (net.is3d && net.stages) {                 // keep the plane labels in frame
    for (const z of Object.values(STAGE_Z)) {
      for (const [dx, dy] of [[-308, -308], [308, -308], [308, 334], [-308, 334]]) {
        const q = project({ x: dx, y: dy, z });
        xs.push(q.x); ys.push(q.y);
      }
    }
  }
  const w = Math.max(...xs) - Math.min(...xs) + 110, h = Math.max(...ys) - Math.min(...ys) + 110;
  net.k = Math.max(.2, Math.min(2.4, Math.min(net.w / w, net.h / h)));
  net.tx = -((Math.max(...xs) + Math.min(...xs)) / 2) * net.k;
  net.ty = -((Math.max(...ys) + Math.min(...ys)) / 2) * net.k;
  draw();
}
$('#netFit').onclick = fitNet;
window.focusOnNetwork = acc => { net.focus = new Set([acc]); net.focusLabel = acc; show('network'); buildNet(); };
window.show = show;


/* ======================================================== HARD NEGATIVES */
function renderHardNegatives() {
  const d = S.decoys || {};
  const el = $('#hardNegatives');
  if (!d.present) { el.innerHTML = '<div class="empty">This ledger has no planted hard negatives.</div>'; }
  else {
    const good = d.discrimination >= 0.95;
    el.innerHTML = `
      <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:12px">
        <span class="mono" style="font-size:30px;color:${good ? 'var(--ok)' : 'var(--high)'}">${(d.discrimination * 100).toFixed(0)}%</span>
        <span class="muted">of look-alike accounts correctly <b>not</b> alerted
          - ${d.total - d.caught} of ${d.total} survived</span>
      </div>
      <table><thead><tr><th style="cursor:default">Lawful activity</th>
        <th class="num" style="cursor:default">Accounts</th><th class="num" style="cursor:default">Wrongly flagged</th>
        <th class="num" style="cursor:default">Discrimination</th><th style="cursor:default;width:150px"></th></tr></thead><tbody>
      ${d.families.map(f => `<tr>
        <td>${esc(f.family)}${f.caught ? `<div class="faint mono" style="font-size:11px">${f.caught_accounts.map(esc).join(', ')}</div>` : ''}</td>
        <td class="num">${f.accounts}</td>
        <td class="num" style="color:${f.caught ? 'var(--crit)' : 'var(--ok)'}">${f.caught}</td>
        <td class="num">${f.discrimination.toFixed(2)}</td>
        <td><div class="bar"><i style="width:${f.discrimination * 100}%;background:${f.discrimination === 1 ? 'var(--ok)' : 'var(--high)'}"></i></div></td>
      </tr>`).join('')}</tbody></table>
      <p class="sub" style="margin-top:10px">${esc(d.note)}</p>`;
  }

  const da = S.discriminator_ablation || [];
  $('#discAblation').innerHTML = da.length ? `<table><thead><tr>
      <th style="cursor:default">Configuration</th><th class="num" style="cursor:default">P</th>
      <th class="num" style="cursor:default">R</th><th class="num" style="cursor:default">FP</th>
      <th class="num" style="cursor:default">Look-alikes flagged</th>
      <th style="cursor:default;width:130px">Precision</th></tr></thead><tbody>
    ${da.map((r, i) => {
      const last = i === da.length - 1, first = i === 0;
      return `<tr style="${last ? 'background:rgba(255,77,109,.08)' : first ? 'background:rgba(46,204,143,.07)' : ''}">
        <td>${esc(r.name)}${r.why ? `<div class="faint" style="font-size:11.5px;margin-top:2px">${esc(r.why)}</div>` : ''}</td>
        <td class="num"><b>${r.precision.toFixed(2)}</b></td>
        <td class="num">${r.recall.toFixed(2)}</td>
        <td class="num" style="color:${r.fp ? 'var(--high)' : 'var(--muted)'}">${r.fp}</td>
        <td class="num" style="color:${r.decoys_caught ? 'var(--crit)' : 'var(--ok)'}">${r.decoys_caught}/${r.decoys_total}</td>
        <td><div class="bar"><i style="width:${r.precision * 100}%;background:${r.precision > .95 ? 'var(--ok)' : r.precision > .8 ? 'var(--med)' : 'var(--crit)'}"></i></div></td>
      </tr>`;
    }).join('')}</tbody></table>` : '<div class="empty">Needs a labelled ledger with planted hard negatives.</div>';
}

/* =============================================================== REPLAY */
let RP = null, rpDay = 1, rpTimer = null;

async function loadReplay() {
  if (RP) return RP;
  busy(true, 'Replaying the ledger day by day - the pipeline runs once per day…');
  try {
    RP = await api('/api/replay');
    rpDay = RP.max_day;
    $('#tagLatency').textContent = RP.summary.mean_latency_days != null
      ? RP.summary.mean_latency_days + 'd' : '–';
    $('#rpScrub').max = RP.max_day;
    $('#rpScrub').value = rpDay;
    renderReplay();
  } catch (e) { toast('Replay failed: ' + e.message); }
  finally { busy(false); }
  return RP;
}
function renderReplay() {
  if (!RP) return;
  const s = RP.summary;
  $('#replayKpis').innerHTML = [
    ['Mean detection lag', s.mean_latency_days != null ? s.mean_latency_days + ' days' : '—',
     'from first transaction to first possible alert', 'high'],
    ['Median lag', s.median_latency_days != null ? s.median_latency_days + ' days' : '—', 'typical case', ''],
    ['Worst lag', s.worst_latency_days != null ? s.worst_latency_days + ' days' : '—',
     'slowest account to become detectable', 'crit'],
    ['First alert', s.first_alert_day ? 'day ' + s.first_alert_day : '—', 'earliest the system could act', 'ok'],
    ['Eventually detected', `${s.detected}`, `${s.never_detected} never surfaced`, 'ok'],
  ].map(([k, v, n, c]) => `<div class="kpi ${c}"><div class="k">${esc(k)}</div>
      <div class="v">${esc(v)}</div><div class="n">${esc(n)}</div></div>`).join('');

  $('#latencyTable tbody').innerHTML = RP.latency.length ? RP.latency.map(l => {
    const worst = RP.summary.worst_latency_days || 1;
    return `<tr><td class="acct" style="cursor:pointer" onclick="selectAccount('${esc(l.account)}');show('alerts')">${esc(l.account)}</td>
      <td class="faint" style="font-size:11.5px">${esc(l.typology)}</td>
      <td class="num">d${l.first_active_day}</td><td class="num">d${l.detected_day}</td>
      <td class="num"><b>${l.latency_days}</b></td>
      <td><div class="bar"><i style="width:${100 * l.latency_days / worst}%;background:${l.latency_days > 7 ? 'var(--crit)' : l.latency_days > 3 ? 'var(--high)' : 'var(--ok)'}"></i></div></td></tr>`;
  }).join('') : '<tr><td colspan="6" class="empty">Nothing detected.</td></tr>';

  drawReplayChart();
  renderReplayFrame();
  renderReplayInsight();
}

function renderReplayInsight() {
  const F = RP.frames.filter(f => f.precision != null && f.flagged > 0);
  if (!F.length) { $('#rpInsight').innerHTML = '<div class="empty">Needs a labelled ledger.</div>'; return; }
  const first = F[0], last = F[F.length - 1];
  const perfect = F.find(f => f.precision >= 0.999);
  const worst = F.reduce((a, b) => (b.precision < a.precision ? b : a), F[0]);
  const spark = (() => {
    const W = 260, H = 44;
    const pts = F.map((f, i) => [8 + i * (W - 16) / (F.length - 1 || 1), H - 6 - f.precision * (H - 12)]);
    const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
    return `<svg viewBox="0 0 ${W} ${H}" style="width:${W}px;height:${H}px">
      <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2"/>
      <line x1="8" x2="${W - 8}" y1="${H - 6}" y2="${H - 6}" stroke="#243149"/></svg>`;
  })();
  $('#rpInsight').innerHTML = `
    <div style="display:flex;align-items:center;gap:20px;flex-wrap:wrap;margin-bottom:12px">
      <div><div class="faint" style="font-size:10.5px;letter-spacing:.6px;text-transform:uppercase">First alert day ${first.day}</div>
        <div class="mono" style="font-size:24px;color:var(--crit)">${first.precision.toFixed(2)}</div></div>
      <div class="faint" style="font-size:20px">→</div>
      <div><div class="faint" style="font-size:10.5px;letter-spacing:.6px;text-transform:uppercase">Day ${last.day} (batch equivalent)</div>
        <div class="mono" style="font-size:24px;color:var(--ok)">${last.precision.toFixed(2)}</div></div>
      <div style="margin-left:auto">${spark}</div>
    </div>
    <p style="font-size:13px;line-height:1.7;margin:0">
      Batch scoring reports <b>${last.precision.toFixed(2)}</b> precision. Running the same detector as a
      stream shows it was only <b>${worst.precision.toFixed(2)}</b> at its worst (day ${worst.day}), and did not
      reach the batch figure until <b>day ${perfect ? perfect.day : last.day}</b>.
      That gap is not a bug - it is the honest cost of acting early.</p>
    <p style="font-size:13px;line-height:1.7;margin:9px 0 0" class="muted">
      The reason is that the look-alike discriminators are evidence-hungry. A settlement rail is
      <em>indistinguishable</em> from a money mule until you have watched it settle three times; a
      contractor with its own trade looks like a dedicated conduit until its unrelated invoices arrive.
      Early in the window that history does not exist yet, so lawful businesses are provisionally
      flagged and then correctly released as evidence accumulates.</p>
    <p style="font-size:13px;line-height:1.7;margin:9px 0 0" class="faint">
      Any AML result quoted without this curve is quoting the easiest number available. The operational
      question is not "what is your F1 at month end" but "what would you have frozen on day
      ${first.day}, and how much of it was innocent".</p>`;
}
function drawReplayChart() {
  const svg = $('#rpChart');
  if (!RP || !RP.frames.length) return;
  const F = RP.frames;
  const W = svg.clientWidth || 900, H = 280, P = { t: 14, r: 46, b: 26, l: 54 };
  const iw = W - P.l - P.r, ih = H - P.t - P.b;
  const maxA = Math.max(1, ...F.map(f => f.flagged));
  const bw = Math.max(2, iw / F.length - 3);
  const x = i => P.l + (i + .5) * (iw / F.length);
  let g = '';
  for (let i = 0; i <= 4; i++) {
    const yy = P.t + ih - (i / 4) * ih;
    g += `<line x1="${P.l}" x2="${W - P.r}" y1="${yy}" y2="${yy}" stroke="#243149"/>
          <text x="${P.l - 8}" y="${yy + 4}" text-anchor="end" fill="#63769a" font-size="10" font-family="monospace">${Math.round(maxA * i / 4)}</text>`;
  }
  F.forEach((f, i) => {
    const future = f.day > rpDay;
    let base = P.t + ih;
    [['MEDIUM', '#ffd93d'], ['HIGH', '#ff9f43'], ['CRITICAL', '#ff4d6d']].forEach(([k, c]) => {
      const n = f.tiers[k]; if (!n) return;
      const hh = (n / maxA) * ih; base -= hh;
      g += `<rect x="${x(i) - bw / 2}" y="${base}" width="${bw}" height="${hh}" fill="${c}"
              opacity="${future ? .13 : .92}" rx="1"><title>Day ${f.day}: ${n} ${k}</title></rect>`;
    });
    if (f.new_alerts.length && !future)
      g += `<circle cx="${x(i)}" cy="${P.t + 6}" r="3" fill="#3ddbd9"><title>Day ${f.day}: ${f.new_alerts.length} new - ${f.new_alerts.join(', ')}</title></circle>`;
    if (f.day % 5 === 0 || i === 0)
      g += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle" fill="#63769a" font-size="10" font-family="monospace">${f.day}</text>`;
  });
  const upto = F.filter(f => f.day <= rpDay);
  const line = (key, colour) => {
    const pts = upto.filter(f => f[key] != null);
    if (pts.length < 2) return '';
    const d = pts.map((f, i) => `${i ? 'L' : 'M'}${x(F.indexOf(f)).toFixed(1)},${(P.t + ih - f[key] * ih).toFixed(1)}`).join(' ');
    return `<path d="${d}" fill="none" stroke="${colour}" stroke-width="1.8"/>`;
  };
  g += line('recall', '#2ecc8f') + line('precision', '#3ddbd9');
  g += `<line x1="${x(rpDay - 1)}" x2="${x(rpDay - 1)}" y1="${P.t}" y2="${P.t + ih}" stroke="#e6edf7" stroke-width="1" stroke-dasharray="3 3" opacity=".55"/>`;
  for (let i = 0; i <= 2; i++) {
    const yy = P.t + ih - (i / 2) * ih;
    g += `<text x="${W - P.r + 8}" y="${yy + 4}" fill="#63769a" font-size="10" font-family="monospace">${(i / 2).toFixed(1)}</text>`;
  }
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.innerHTML = g;
}
function renderReplayFrame() {
  if (!RP) return;
  const f = RP.frames.find(x => x.day === rpDay) || RP.frames[RP.frames.length - 1];
  $('#rpDayLabel').textContent = `day ${f.day} · ${f.date}`;
  $('#rpFrameDay').textContent = f.day;
  const cum = RP.frames.filter(x => x.day <= f.day).flatMap(x => x.new_alerts);
  $('#rpFrame').innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">
      ${[['Transactions seen', f.txns_seen], ['Value seen', inrShort(f.value_seen)],
         ['Accounts alerted', f.flagged],
         ['Precision / recall', f.precision != null ? `${f.precision.toFixed(2)} / ${f.recall.toFixed(2)}` : '—']]
        .map(([k, v]) => `<div style="background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 10px">
          <div class="faint" style="font-size:10.5px;text-transform:uppercase;letter-spacing:.6px">${esc(k)}</div>
          <div class="mono" style="font-size:14px;margin-top:2px">${esc(v)}</div></div>`).join('')}
    </div>
    <div style="margin-bottom:10px">${['CRITICAL', 'HIGH', 'MEDIUM'].map(t =>
      `<span class="pill ${t}">${t} ${f.tiers[t]}</span> `).join('')}</div>
    ${f.new_alerts.length ? `<div><span class="chip p">new today</span>
      ${f.new_alerts.map(a => `<span class="chip" style="cursor:pointer;color:var(--crit)" onclick="selectAccount('${esc(a)}');show('alerts')">${esc(a)}</span>`).join('')}</div>`
      : '<div class="faint" style="font-size:12.5px">No new accounts became detectable on this day.</div>'}
    <div style="margin-top:12px" class="faint" style="font-size:12px">Alerted so far (${cum.length}):</div>
    <div style="max-height:150px;overflow:auto;margin-top:5px">${cum.map(a => `<span class="chip">${esc(a)}</span>`).join('') || '<span class="faint">none</span>'}</div>`;
}
function rpSet(day) {
  rpDay = Math.max(1, Math.min(RP ? RP.max_day : 1, day));
  $('#rpScrub').value = rpDay;
  drawReplayChart(); renderReplayFrame();
}
$('#rpScrub').oninput = e => rpSet(+e.target.value);
$('#rpStep').onclick = () => rpSet(rpDay + 1);
$('#rpReset').onclick = () => { stopPlay(); rpSet(1); };
function stopPlay() { clearInterval(rpTimer); rpTimer = null; $('#rpPlay').textContent = '▶ Play'; }
$('#rpPlay').onclick = () => {
  if (rpTimer) return stopPlay();
  if (rpDay >= RP.max_day) rpSet(1);
  $('#rpPlay').textContent = '❚❚ Pause';
  rpTimer = setInterval(() => {
    if (rpDay >= RP.max_day) return stopPlay();
    rpSet(rpDay + 1);
  }, +$('#rpSpeed').value);
};
$('#rpSpeed').onchange = () => { if (rpTimer) { stopPlay(); $('#rpPlay').click(); } };

/* ============================================================ ECONOMICS */
let ECON = null;
async function loadEconomics() {
  const rate = +$('#econRate').value, mult = +$('#econMult').value;
  $('#econRateV').textContent = inr(rate);
  $('#econMultV').textContent = mult + '×';
  try {
    ECON = await api('/api/economics', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ analyst_cost_per_hour: rate, portfolio_multiplier: mult }) });
    renderEconomics(); renderEconPie();
  } catch (e) { toast('Economics failed: ' + e.message); }
}
function renderEconomics() {
  const e = ECON; if (!e) return;
  const fpb = e.false_positive_burden;
  $('#econKpis').innerHTML = [
    ['Alerts per day', e.alerts_per_day, `${e.alerts_per_year.toLocaleString('en-IN')} a year`, 'high'],
    ['Analyst headcount', e.analyst_fte + ' FTE', `${e.review_hours_year.toLocaleString('en-IN')} review hours a year`, 'crit'],
    ['Annual review cost', inrShort(e.annual_review_cost), `${inr(e.cost_per_alert)} per alert`, 'crit'],
    ['Value at risk surfaced', inrShort(e.value_at_risk), 'exposure touching alerted accounts', 'ok'],
    ['Return per analyst hour', inrShort(e.return_per_analyst_hour), 'illicit flow surfaced per hour worked', 'ok'],
    e.conversion_rate != null
      ? ['Conversion rate', (e.conversion_rate * 100).toFixed(1) + '%', 'alerts that become a filing', 'ok']
      : ['Conversion rate', '—', 'needs labels', ''],
  ].map(([k, v, n, c]) => `<div class="kpi ${c}"><div class="k">${esc(k)}</div>
      <div class="v">${esc(v)}</div><div class="n">${esc(n)}</div></div>`).join('');

  $('#econMinutes').innerHTML = Object.entries(e.assumptions.review_minutes_by_tier)
    .map(([k, v]) => `<span class="chip">${k} ${v}m</span>`).join('');

  const t = S.summary.tiers, mins = e.assumptions.review_minutes_by_tier;
  const effort = Object.entries(t).map(([k, n]) => [k, n * mins[k] / 60]).filter(([, h]) => h > 0);
  const totalH = effort.reduce((a, [, h]) => a + h, 0) || 1;
  $('#econEffort').innerHTML = effort.map(([k, h]) => `
    <div style="margin-bottom:11px">
      <div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px">
        <span><span class="pill ${k}">${k}</span> ${t[k]} alerts</span>
        <span class="mono">${h.toFixed(1)} h · ${(100 * h / totalH).toFixed(0)}%</span></div>
      <div class="bar"><i style="width:${100 * h / totalH}%;background:${TIER_COLOR[k]}"></i></div></div>`).join('') +
    (fpb ? `<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--line)">
      <div style="display:flex;justify-content:space-between;font-size:12.5px">
        <span class="muted">Hours spent on accounts that turn out clean</span>
        <span class="mono" style="color:${fpb.share_of_review_effort > .3 ? 'var(--crit)' : 'var(--ok)'}">
          ${fpb.wasted_hours_window.toFixed(1)} h (${(fpb.share_of_review_effort * 100).toFixed(0)}%)</span></div>
      <div class="faint" style="font-size:12px;margin-top:4px">
        ${fpb.false_positives} false positive(s) · ${inrShort(fpb.wasted_cost_year)} a year at these assumptions</div>
    </div>` : '');

  if (e.conversion_rate != null) {
    const ours = e.conversion_rate, ind = e.industry_comparison.typical_bank_conversion;
    $('#econConversion').innerHTML = `
      <div style="margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px">
          <span>This configuration</span><span class="mono" style="color:var(--ok)">${(ours * 100).toFixed(1)}%</span></div>
        <div class="bar"><i style="width:${ours * 100}%;background:var(--ok)"></i></div></div>
      <div>
        <div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px">
          <span class="muted">Typical large bank</span><span class="mono muted">${(ind * 100).toFixed(0)}%</span></div>
        <div class="bar"><i style="width:${ind * 100}%;background:var(--muted)"></i></div></div>
      <p class="sub" style="margin-top:11px">${esc(e.industry_comparison.note)}</p>`;
  } else $('#econConversion').innerHTML = '<div class="empty">Needs a labelled ledger.</div>';

  $('#econNarrative').innerHTML = `
    <p>At ${inr(+$('#econRate').value)} an hour and a ${$('#econMult').value}× portfolio, this
    configuration needs <b>${e.analyst_fte} full-time analysts</b> and costs
    <b>${inrShort(e.annual_review_cost)}</b> a year to review.</p>
    ${fpb ? `<p>Of that, <b>${(fpb.share_of_review_effort * 100).toFixed(0)}%</b> of effort,
      ${inrShort(fpb.wasted_cost_year)} a year, goes on accounts that are ultimately clean.
      Loosening a threshold moves that number faster than it moves recall.</p>` : ''}
    <p class="faint">Every slider in the Detection Lab is really a budget decision. That is the
    trade-off this panel exists to make visible.</p>`;
}
$('#econRate').oninput = () => { $('#econRateV').textContent = inr(+$('#econRate').value); };
$('#econMult').oninput = () => { $('#econMultV').textContent = $('#econMult').value + '×'; };
$('#econRate').onchange = loadEconomics;
$('#econMult').onchange = loadEconomics;

$('#frontierBtn').onclick = async () => {
  const btn = $('#frontierBtn'); btn.disabled = true;
  const keepCfg = JSON.parse(JSON.stringify(S.config));
  const rows = [];
  busy(true, 'Recomputing the pipeline across the threshold range…');
  try {
    for (const fb of [2, 3, 4, 5]) {
      $('#loadmsg').textContent = `Fan-branch threshold ${fb}…`;
      const r = await api('/api/reconfigure', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: { ...keepCfg, fan_min_branches: fb }, ablation: false }) });
      rows.push({ fb, alerts: r.summary.flagged, rings: r.summary.rings,
        p: r.metrics.labelled ? r.metrics.precision : null,
        rec: r.metrics.labelled ? r.metrics.recall : null,
        fte: r.economics.analyst_fte, cost: r.economics.annual_review_cost,
        illicit: r.summary.illicit_flow });
    }
    S = await api('/api/reconfigure', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config: keepCfg, ablation: true }) });
    renderAll();
    const maxCost = Math.max(...rows.map(r => r.cost)) || 1;
    $('#frontier').innerHTML = `<table><thead><tr>
        <th style="cursor:default">Fan-branch threshold</th><th class="num" style="cursor:default">Alerts</th>
        <th class="num" style="cursor:default">Recall</th><th class="num" style="cursor:default">Precision</th>
        <th class="num" style="cursor:default">Analyst FTE</th><th class="num" style="cursor:default">Annual cost</th>
        <th style="cursor:default;width:150px">Cost</th></tr></thead><tbody>
      ${rows.map(r => `<tr${r.fb === keepCfg.fan_min_branches ? ' style="background:rgba(61,219,217,.08)"' : ''}>
        <td class="mono">${r.fb}${r.fb === keepCfg.fan_min_branches ? ' <span class="chip p">current</span>' : ''}</td>
        <td class="num">${r.alerts}</td>
        <td class="num">${r.rec != null ? r.rec.toFixed(2) : '—'}</td>
        <td class="num">${r.p != null ? r.p.toFixed(2) : '—'}</td>
        <td class="num">${r.fte}</td>
        <td class="num">${inrShort(r.cost)}</td>
        <td><div class="bar"><i style="width:${100 * r.cost / maxCost}%"></i></div></td></tr>`).join('')}
      </tbody></table>
      <p class="sub" style="margin-top:10px">Tightening the rule cuts headcount and cost, and cuts recall
        with it. There is no free precision - that is the whole argument for making the threshold an
        explicit, costed decision rather than a hard-coded constant.</p>`;
    $('#frontierStatus').textContent = 'Frontier computed.';
  } catch (e) { toast('Frontier failed: ' + e.message); }
  finally { busy(false); btn.disabled = false; }
};

/* ------------------------------------------------------- schema listing */
async function renderSchemas() {
  try {
    const list = await api('/api/schemas');
    $('#schemaList').innerHTML = list.map(s =>
      `<div style="padding:6px 0;border-bottom:1px solid var(--line)">
        <span class="chip p">${esc(s.key)}</span>
        <span style="font-size:12.5px">${esc(s.label)}</span>
        <div class="faint mono" style="font-size:11px;margin-top:2px">${esc(s.note)}</div>
      </div>`).join('');
  } catch (e) { /* non-fatal */ }
}


/* ==================================================== AMBIENCE / PARALLAX */
const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* Scroll parallax: three fixed layers move at different fractions of the
   scroll distance. Transform-only, so it never triggers layout. */
(function parallax() {
  if (REDUCED) return;
  const layers = [[$('#sky .l1'), 0.06], [$('#sky .l2'), 0.13], [$('#sky .l3'), 0.22]];
  let pending = false;
  $('#main').addEventListener('scroll', () => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      const y = $('#main').scrollTop;
      layers.forEach(([el, k]) => { if (el) el.style.transform = `translate3d(0,${-y * k}px,0)`; });
      pending = false;
    });
  }, { passive: true });
  // pointer parallax - a couple of pixels, just enough to feel like depth
  window.addEventListener('pointermove', e => {
    const dx = (e.clientX / innerWidth - .5), dy = (e.clientY / innerHeight - .5);
    const l1 = $('#sky .l1'), l2 = $('#sky .l2');
    if (l1) l1.style.marginLeft = (dx * 14).toFixed(1) + 'px';
    if (l2) l2.style.marginLeft = (dx * -22).toFixed(1) + 'px';
    if (l1) l1.style.marginTop = (dy * 10).toFixed(1) + 'px';
    if (l2) l2.style.marginTop = (dy * -16).toFixed(1) + 'px';
  }, { passive: true });
})();

/* Star field: slow-drifting points at three depths. Pure canvas, ~2% of a frame. */
(function stars() {
  const c = $('#stars'); if (!c) return;
  const x = c.getContext('2d');
  let pts = [], w = 0, hh = 0;
  function size() {
    const dpr = Math.min(2, devicePixelRatio || 1);
    w = innerWidth; hh = innerHeight;
    c.width = w * dpr; c.height = hh * dpr;
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
    pts = Array.from({ length: Math.min(150, Math.round(w * hh / 14000)) }, () => ({
      x: Math.random() * w, y: Math.random() * hh,
      z: Math.random() * .8 + .2, r: Math.random() * 1.25 + .3,
      p: Math.random() * Math.PI * 2,
    }));
  }
  size(); addEventListener('resize', size);
  let t = 0;
  (function loop() {
    requestAnimationFrame(loop);
    if (REDUCED) { return; }
    t += 0.006;
    x.clearRect(0, 0, w, hh);
    for (const s of pts) {
      s.y -= s.z * 0.12;
      if (s.y < -2) { s.y = hh + 2; s.x = Math.random() * w; }
      const tw = 0.45 + 0.55 * Math.abs(Math.sin(t + s.p));
      x.globalAlpha = tw * s.z * 0.8;
      x.fillStyle = s.z > .7 ? '#8fd8ff' : '#4d7cff';
      x.beginPath(); x.arc(s.x, s.y, s.r * s.z, 0, 7); x.fill();
    }
    x.globalAlpha = 1;
  })();
})();

/* Hero: money-flow particles streaming along bezier paths behind the title. */
function heroCanvas() {
  const c = $('#heroCanvas'); if (!c) return;
  const x = c.getContext('2d');
  let w = 0, hh = 0, paths = [], parts = [];
  function size() {
    const r = c.getBoundingClientRect(); if (!r.width) return;
    const dpr = Math.min(2, devicePixelRatio || 1);
    w = r.width; hh = r.height;
    c.width = w * dpr; c.height = hh * dpr;
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
    paths = Array.from({ length: 7 }, (_, i) => {
      const y0 = hh * (0.12 + 0.12 * i) + Math.sin(i) * 10;
      return { x0: -40, y0, x1: w * .42, y1: y0 - 46 + i * 12,
               x2: w * .74, y2: y0 + 40 - i * 9, x3: w + 40, y3: hh * (0.2 + 0.1 * i) };
    });
    parts = Array.from({ length: 46 }, () => ({
      p: Math.floor(Math.random() * paths.length), t: Math.random(),
      v: 0.0016 + Math.random() * 0.0032, r: 1 + Math.random() * 2.1,
    }));
  }
  const bez = (a, b, cc, d, t) => {
    const u = 1 - t;
    return u * u * u * a + 3 * u * u * t * b + 3 * u * t * t * cc + t * t * t * d;
  };
  size(); addEventListener('resize', size);
  (function loop() {
    requestAnimationFrame(loop);
    if (!w) { size(); return; }
    x.clearRect(0, 0, w, hh);
    x.lineWidth = 1;
    paths.forEach((p, i) => {
      x.strokeStyle = `rgba(77,124,255,${0.10 + i * 0.012})`;
      x.beginPath(); x.moveTo(p.x0, p.y0);
      x.bezierCurveTo(p.x1, p.y1, p.x2, p.y2, p.x3, p.y3); x.stroke();
    });
    for (const s of parts) {
      if (!REDUCED) s.t += s.v;
      if (s.t > 1) { s.t = 0; s.p = Math.floor(Math.random() * paths.length); }
      const p = paths[s.p]; if (!p) continue;
      const px = bez(p.x0, p.x1, p.x2, p.x3, s.t), py = bez(p.y0, p.y1, p.y2, p.y3, s.t);
      const g = x.createRadialGradient(px, py, 0, px, py, s.r * 5);
      g.addColorStop(0, 'rgba(56,224,216,.95)'); g.addColorStop(1, 'rgba(56,224,216,0)');
      x.fillStyle = g; x.beginPath(); x.arc(px, py, s.r * 5, 0, 7); x.fill();
      x.fillStyle = '#bff7f4'; x.beginPath(); x.arc(px, py, s.r * .7, 0, 7); x.fill();
    }
  })();
}

function renderHero() {
  const d = S.dataset, s = S.summary, m = S.metrics;
  $('#heroTags').innerHTML = [
    [`${d.accounts}`, 'accounts screened'],
    [`${d.transactions}`, 'transactions'],
    [`${s.flagged}`, 'alerted'],
    [`${s.rings}`, 'rings'],
    [inrShort(s.illicit_flow), 'illicit flow traced'],
    m.labelled ? [`${m.precision.toFixed(2)}/${m.recall.toFixed(2)}`, 'precision / recall'] : null,
    S.decoys && S.decoys.present
      ? [`${S.decoys.total - S.decoys.caught}/${S.decoys.total}`, 'look-alikes correctly ignored'] : null,
  ].filter(Boolean).map(([v, l]) => `<span><b>${esc(v)}</b> ${esc(l)}</span>`).join('');
}

/* ============================================================ PIE CHARTS */
/* One reusable donut/pie. Hovering a legend row dims the other slices, which
   is the only way a pie is genuinely readable with more than three parts. */
function pie(el, legendEl, rows, opts = {}) {
  const total = rows.reduce((a, r) => a + r.value, 0);
  const R = opts.r || 74, IN = opts.inner === 0 ? 0 : (opts.inner || 44), C = R + 6;
  const host = typeof el === 'string' ? $(el) : el;
  const leg = typeof legendEl === 'string' ? $(legendEl) : legendEl;
  if (!host) return;
  if (!total) { host.innerHTML = '<div class="empty">No data</div>'; if (leg) leg.innerHTML = ''; return; }

  let a0 = -Math.PI / 2, paths = '';
  rows.forEach((r, i) => {
    const frac = r.value / total, a1 = a0 + frac * Math.PI * 2;
    const big = frac > .5 ? 1 : 0;
    const p = (rad, ang) => [(C + rad * Math.cos(ang)).toFixed(2), (C + rad * Math.sin(ang)).toFixed(2)];
    const [ox0, oy0] = p(R, a0), [ox1, oy1] = p(R, a1);
    let d;
    if (frac >= 0.9999) {
      d = `M ${C} ${C - R} A ${R} ${R} 0 1 1 ${C - 0.01} ${C - R} Z`;
      if (IN) d += ` M ${C} ${C - IN} A ${IN} ${IN} 0 1 0 ${C - 0.01} ${C - IN} Z`;
    } else if (IN) {
      const [ix1, iy1] = p(IN, a1), [ix0, iy0] = p(IN, a0);
      d = `M ${ox0} ${oy0} A ${R} ${R} 0 ${big} 1 ${ox1} ${oy1} L ${ix1} ${iy1} A ${IN} ${IN} 0 ${big} 0 ${ix0} ${iy0} Z`;
    } else {
      d = `M ${C} ${C} L ${ox0} ${oy0} A ${R} ${R} 0 ${big} 1 ${ox1} ${oy1} Z`;
    }
    paths += `<path data-i="${i}" d="${d}" fill="${r.color}" fill-rule="evenodd" opacity=".92"
        stroke="rgba(4,6,13,.85)" stroke-width="1.5"><title>${esc(r.label)}: ${esc(r.display || r.value)} (${(frac * 100).toFixed(1)}%)</title></path>`;
    a0 = a1;
  });
  const centre = IN ? `<text x="${C}" y="${C - 2}" text-anchor="middle" fill="#e8eefb"
      font-size="23" font-family="ui-monospace,monospace">${esc(opts.centre ?? total)}</text>
    <text x="${C}" y="${C + 15}" text-anchor="middle" fill="#6a7fa6" font-size="9"
      letter-spacing="1.4">${esc(opts.centreLabel || '')}</text>` : '';
  host.innerHTML = `<svg class="pie" viewBox="0 0 ${C * 2} ${C * 2}"
      style="width:${C * 2}px;height:${C * 2}px;display:block">${paths}${centre}</svg>`;

  if (leg) {
    leg.innerHTML = rows.map((r, i) => `<div data-i="${i}">
        <i style="background:${r.color};color:${r.color}"></i>
        <span>${esc(r.label)}</span>
        <span class="v">${esc(r.display ?? r.value)}</span></div>`).join('');
    const slices = [...host.querySelectorAll('path')];
    const focus = i => slices.forEach(s => s.classList.toggle('dim', i != null && +s.dataset.i !== i));
    [...leg.children].forEach(row => {
      row.onmouseenter = () => focus(+row.dataset.i);
      row.onmouseleave = () => focus(null);
      if (rows[+row.dataset.i].onClick) row.onclick = rows[+row.dataset.i].onClick;
    });
    slices.forEach(s => {
      s.onmouseenter = () => focus(+s.dataset.i);
      s.onmouseleave = () => focus(null);
    });
  }
}

function renderPies() {
  // risk tiers
  const t = S.summary.tiers;
  pie('#tierChart', '#tierRows',
    ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].filter(k => t[k]).map(k => ({
      label: k, value: t[k], color: TIER_COLOR[k], display: t[k],
      onClick: () => { $('#alertTier').value = k; drawAlertRows(); show('alerts'); },
    })),
    { centre: S.summary.flagged, centreLabel: 'ALERTS' });

  // laundering stages
  const st = { PLACEMENT: 0, LAYERING: 0, INTEGRATION: 0 };
  S.timeline.events.forEach(e => st[e.stage] += e.amount);
  pie('#stagePie', '#stageLegend',
    Object.entries(st).filter(([, v]) => v).map(([k, v]) => ({
      label: k[0] + k.slice(1).toLowerCase(), value: v, color: STAGE_COLOR[k], display: inrShort(v),
      onClick: () => { $('#evStage').value = k; renderEventStream(); show('timeline'); },
    })),
    { centre: inrShort(Object.values(st).reduce((a, b) => a + b, 0)), centreLabel: 'TRACED', r: 68, inner: 40 });

  // typology mix
  const PAL = ['#38e0d8', '#4d7cff', '#8b5cf6', '#ff9f43', '#2ecc8f', '#ff4d6d', '#ffd93d'];
  const byTyp = {};
  S.alerts.forEach(a => byTyp[a.typology] = (byTyp[a.typology] || 0) + 1);
  pie('#typologyPie', '#typologyLegend',
    Object.entries(byTyp).sort((a, b) => b[1] - a[1]).map(([k, v], i) => ({
      label: k, value: v, color: PAL[i % PAL.length], display: v,
    })),
    { centre: S.summary.rings, centreLabel: 'RINGS', r: 70, inner: 42 });
}

function renderEconPie() {
  if (!ECON) return;
  const t = S.summary.tiers, mins = ECON.assumptions.review_minutes_by_tier;
  pie('#econPie', '#econPieLegend',
    Object.entries(t).filter(([k, n]) => n && mins[k]).map(([k, n]) => ({
      label: k[0] + k.slice(1).toLowerCase(), value: n * mins[k] / 60,
      color: TIER_COLOR[k], display: (n * mins[k] / 60).toFixed(1) + ' h',
    })),
    { centre: ECON.review_hours_window.toFixed(0), centreLabel: 'HOURS', r: 66, inner: 39 });
}


/* ========================================================== PROVENANCE */
const CONF_COLOR = { reported: 'var(--ok)', regulatory: 'var(--accent2)',
                     derived: 'var(--med)', assumed: 'var(--high)' };

function renderProvenance() {
  const P = S.provenance;
  if (!P) {
    $('#provWhy').textContent = 'This ledger was uploaded, so no calibration applies.';
    $('#provReal').textContent = '—'; $('#provNot').textContent = '—';
    $('#provTable tbody').innerHTML = '<tr><td colspan="4" class="empty">No calibration metadata.</td></tr>';
    $('#provSources').innerHTML = ''; $('#provMix').innerHTML = '';
    return;
  }
  $('#provWhy').textContent = P.why_synthetic;
  $('#provReal').textContent = P.what_is_real;
  $('#provNot').textContent = P.what_is_not;

  $('#provTable tbody').innerHTML = P.parameters.map(r => `
    <tr>
      <td><b>${esc(r.group)}</b>
        <div class="faint" style="font-size:11.5px;margin-top:3px;line-height:1.45">${esc(r.detail)}</div></td>
      <td class="mono" style="font-size:11.5px;max-width:260px">${esc(r.value)}</td>
      <td><span class="chip" style="color:${CONF_COLOR[r.confidence] || 'var(--muted)'};
        border-color:${CONF_COLOR[r.confidence] || 'var(--line)'}">${esc(r.confidence)}</span></td>
      <td class="faint" style="font-size:11.5px">${esc(r.source || '—')}</td>
    </tr>`).join('');

  $('#provSources').innerHTML = Object.entries(P.sources).map(([k, v]) => `
    <div style="padding:8px 0;border-bottom:1px solid var(--line)">
      <span class="chip p">${esc(k)}</span>
      <b style="font-size:13px">${esc(v.label)}</b>
      <span class="faint" style="font-size:12px"> · ${esc(v.period)}</span>
      <div class="faint" style="font-size:11.5px;margin-top:3px">${esc(v.note)}</div>
      <a href="${esc(v.url)}" target="_blank" rel="noopener"
         class="mono" style="font-size:11px;color:var(--accent2)">${esc(v.url)}</a>
    </div>`).join('');

  // observed vs published channel mix, ordinary traffic only
  const roles = S.roles || {};
  const alerted = new Set(S.alerts.map(a => a.account));
  const counts = {};
  let total = 0;
  S.graph.edges.forEach(() => {});
  (S.ledger_channel_mix || []).forEach(() => {});
  const mixRows = S.channel_mix || null;
  if (!mixRows) { $('#provMix').innerHTML = '<div class="empty">Channel mix unavailable.</div>'; return; }
  $('#provMix').innerHTML = mixRows.map(r => `
    <div style="margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px">
        <span><b>${esc(r.channel)}</b> <span class="faint">${esc(r.note || '')}</span></span>
        <span class="mono">ledger ${r.observed.toFixed(1)}%  ·  RBI ${r.published.toFixed(1)}%</span></div>
      <div style="position:relative">
        <div class="bar"><i style="width:${Math.min(100, r.observed)}%"></i></div>
        <div style="position:absolute;top:-2px;left:${Math.min(100, r.published)}%;width:2px;height:11px;
             background:var(--high);box-shadow:0 0 6px var(--high)" title="RBI published share"></div>
      </div>
    </div>`).join('') +
    '<p class="sub" style="margin-top:10px">Bar: this ledger. Orange marker: the RBI-published ' +
    'national share. Divergence is sampling noise on 150 ordinary transactions.</p>';
}

/* ------------------------------------------------------------------ boot */
(async function boot() {
  try {
    busy(true, 'Running nine detection layers over the ledger…');
    S = await api('/api/state');
    sizeCanvas();
    heroCanvas();
    renderAll();
    renderSchemas();
    sync3dUi();
    wireSoundBtn();
    busy(false);
  } catch (e) {
    busy(true, 'Could not reach the analysis service: ' + e.message);
  }
})();
