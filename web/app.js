/* Sitterwise Payroll — the whole interface.
   Plain JavaScript on purpose: no build step, nothing to install, and the
   file can be read straight through if anyone ever needs to check it. */

const S = { view: 'home', runId: null, run: null, home: null, roster: null,
            settings: null, entryIndex: 0, focusCaregiver: null, filter: 'all' };

/* ---------- helpers ---------- */
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (v) => Number(v ?? 0);
const money = (v) => '$' + num(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const hrs = (v) => num(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function toast(message, bad) {
  const el = document.createElement('div');
  el.className = 'toast' + (bad ? ' bad' : '');
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), bad ? 5200 : 2400);
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const isJson = (res.headers.get('content-type') || '').includes('json');
  const body = isJson ? await res.json() : null;
  if (!res.ok) throw new Error((body && body.error) || `Something went wrong (${res.status})`);
  return body;
}
const post = (path, data) => api(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
});
const del = (path) => api(path, { method: 'DELETE' });

function copy(text, button) {
  navigator.clipboard.writeText(String(text)).then(() => {
    const was = button.textContent;
    button.textContent = 'Copied';
    button.classList.add('copied');
    setTimeout(() => { button.textContent = was; button.classList.remove('copied'); }, 1100);
  });
}

/* ---------- routing ---------- */
function go(view, runId) {
  if (runId !== undefined) S.runId = runId;
  S.view = view;
  location.hash = S.runId && view !== 'home' && view !== 'roster' && view !== 'settings' && view !== 'history'
    ? `#/${view}/${S.runId}` : `#/${view}`;
}
window.addEventListener('hashchange', route);

async function route() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  S.view = parts[0] || 'home';
  if (parts[1]) S.runId = parts[1];
  await render();
}

/* ---------- data ---------- */
async function loadHome() { S.home = await api('/api/state'); }
async function loadRun() {
  if (!S.runId) { S.run = null; return; }
  try { S.run = await api(`/api/runs/${S.runId}`); }
  catch (e) { S.run = null; S.runId = null; toast(e.message, true); }
}
async function refreshRun() { await loadRun(); await render(); }

/* ---------- shell ---------- */
/* The table headings stick just below the top bar, whose height changes when
   the navigation wraps on a narrow window. */
function measureTopbar() {
  const bar = $('.topbar');
  if (bar) document.documentElement.style.setProperty('--topbar-h', bar.offsetHeight + 'px');
}
window.addEventListener('resize', measureTopbar);

function navBar() {
  const run = S.run && S.run.run;
  const summary = S.run && S.run.summary;
  const items = [['home', 'Home']];
  if (run) {
    const stops = summary ? summary.blocked : 0;
    items.push(
      ['check', 'Payroll check' + (stops ? `<span class="pip">${stops}</span>` : '')],
      ['cards', 'Caregivers'],
      ['onpay', 'Enter in OnPay'],
      ['reconcile', 'Check it adds up'],
      ['exports', 'Exports'],
    );
  }
  const open = (S.notes && S.notes.notes.filter(n => n.status === 'open').length) || 0;
  items.push(['notes', 'Notes' + (open ? `<span class="pip quiet">${open}</span>` : '')],
             ['roster', 'Roster'], ['history', 'History'], ['settings', 'Settings']);
  $('#nav').innerHTML = items.map(([key, label]) =>
    `<button onclick="go('${key}')" aria-current="${S.view === key}">${label}</button>`).join('');
}

const VIEWS = {};

async function render() {
  navBar();
  const needsRun = ['check', 'cards', 'onpay', 'entry', 'reconcile', 'exports'];
  if (needsRun.includes(S.view) && (!S.run || S.run.run.id !== S.runId)) await loadRun();
  if (needsRun.includes(S.view) && !S.run) { S.view = 'home'; }
  if (S.view === 'home') await loadHome();
  if (S.view === 'roster') S.roster = await api('/api/roster');
  if (S.view === 'notes' || S.view === 'home') S.notes = await api('/api/notes');
  if (S.view === 'settings') S.recurring = await api('/api/recurring');
  if (S.view === 'settings') S.settings = await api('/api/settings');
  if (S.view === 'history') S.home = await api('/api/state');
  navBar();
  $('#app').innerHTML = (VIEWS[S.view] || VIEWS.home)();
  measureTopbar();
  if (VIEWS['after_' + S.view]) VIEWS['after_' + S.view]();
}

/* =====================================================================
   HOME — upload
   ===================================================================== */
VIEWS.home = () => {
  const runs = (S.home && S.home.runs) || [];
  const open = runs.filter(r => r.status === 'open');
  return `
  <div class="pagehead">
    <h1>Run payroll</h1>
    <p class="sub">Upload the export from Sitterwise. The app does the rest.</p>
  </div>

  <div class="dropzone" id="drop">
    <button class="btn btn-primary btn-huge" onclick="$('#file').click()">
      Upload Sitterwise Payroll Export
    </button>
    <div class="hint">or drop the file here — .xlsx or .csv</div>
    <input type="file" id="file" accept=".xlsx,.xlsm,.csv" hidden>
  </div>
  <div id="uploadresult"></div>

  ${open.length ? `
  <h2 style="margin-top:34px">Carry on where you left off</h2>
  <div class="card" style="padding:0;margin-top:12px">
    ${open.map(runRow).join('')}
  </div>` : ''}

  ${S.home && S.home.roster_needing_attention ? `
  <div class="banner warn" style="margin-top:20px">
    ${plural(S.home.roster_needing_attention, 'caregiver is', 'caregivers are')} not fully set up in
    OnPay. <a href="#/roster">Have a look at the roster</a>.
  </div>` : ''}`;
};

const runRow = (r) => `
  <button class="runrow" onclick="openRun('${r.id}')">
    <div>
      <div class="rl">${esc(r.label)}</div>
      <div class="faint">${esc(r.source_filename || '')}</div>
    </div>
    <span class="pill ${r.status === 'finalized' ? 'entered' : 'plain'}" style="margin-left:auto">
      ${r.status === 'finalized' ? 'Finished and locked' : 'In progress'}
    </span>
  </button>`;

async function openRun(id) { S.runId = id; S.run = null; go('check', id); }

VIEWS.after_home = () => {
  const drop = $('#drop'), input = $('#file');
  input.onchange = () => input.files[0] && upload(input.files[0]);
  ['dragenter', 'dragover'].forEach(e => drop.addEventListener(e, ev => {
    ev.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach(e => drop.addEventListener(e, ev => {
    ev.preventDefault(); drop.classList.remove('over');
  }));
  drop.addEventListener('drop', ev => {
    const file = ev.dataTransfer.files[0];
    if (file) upload(file);
  });
};

async function upload(file) {
  $('#uploadresult').innerHTML = `<div class="card"><p class="muted">Reading ${esc(file.name)}…</p></div>`;
  try {
    const info = await api('/api/upload', {
      method: 'POST', headers: { 'X-Filename': encodeURIComponent(file.name) }, body: file,
    });
    window._upload = info;
    $('#uploadresult').innerHTML = uploadCard(info);
  } catch (e) {
    $('#uploadresult').innerHTML = `<div class="banner bad" style="margin-top:16px">${esc(e.message)}</div>`;
  }
}

function uploadCard(info) {
  const choices = info.period_choices || [];
  return `
  <div class="card" style="margin-top:20px">
    <h2>${esc(info.source_filename)}</h2>
    <p class="sub">${info.jobs} bookings, ${info.payable_jobs} of them payable,
       ${info.caregivers} caregivers, ${esc(info.min_date)} to ${esc(info.max_date)}.</p>

    ${info.suggested.note ? `<div class="banner warn">${esc(info.suggested.note)}</div>` : ''}
    ${info.missing_columns.length ? `<div class="banner bad">
       This file is missing ${esc(info.missing_columns.join(', '))}, which payroll needs.</div>` : ''}
    ${info.parse_errors.length ? `<div class="banner bad">
       ${plural(info.parse_errors.length, 'row', 'rows')} could not be read:
       ${esc(info.parse_errors.slice(0, 3).map(e => `row ${e.row} — ${e.problem}`).join('; '))}</div>` : ''}
    ${info.unmapped_columns.length ? `<div class="banner warn">
       Columns the app did not recognise and has ignored:
       ${esc(info.unmapped_columns.join(', '))}.</div>` : ''}

    <h3 style="margin-top:18px">Which payroll is this?</h3>
    ${periodButtons(choices.filter(c => c.kind !== 'half_month'),
                    'Pay weeks — Monday to Sunday')}
    ${periodButtons(choices.filter(c => c.kind === 'half_month'),
                    'Or a half-month, if you need one')}
    <div class="row" style="margin-top:14px;align-items:flex-end">
      <label class="field" style="margin:0"><span>From</span>
        <input type="date" id="ps" value="${esc(info.suggested.start)}"></label>
      <label class="field" style="margin:0"><span>To</span>
        <input type="date" id="pe" value="${esc(info.suggested.end)}"></label>
      <button class="btn btn-primary" onclick="createRun()">Start this payroll</button>
    </div>
  </div>`;
}

function periodButtons(choices, heading) {
  if (!choices.length) return '';
  return `
    <div style="margin-top:14px">
      <div class="faint" style="margin-bottom:6px">${esc(heading)}</div>
      <div class="row">
        ${choices.map(c => `
          <button class="btn" onclick="pickPeriod('${c.start}','${c.end}')">
            ${esc(c.label)} <span class="faint">· ${c.jobs} jobs</span>
          </button>`).join('')}
      </div>
    </div>`;
}

function pickPeriod(start, end) { $('#ps').value = start; $('#pe').value = end; createRun(); }

async function createRun() {
  try {
    const res = await post('/api/runs', {
      source_path: window._upload.source_path,
      source_filename: window._upload.source_filename,
      period_start: $('#ps').value, period_end: $('#pe').value,
    });
    S.runId = res.run_id; S.run = null;
    go('check', res.run_id);
  } catch (e) { toast(e.message, true); }
}

/* =====================================================================
   PAYROLL CHECK
   ===================================================================== */
VIEWS.check = () => {
  const { summary, findings, run } = S.run;
  const stops = findings.filter(f => f.level === 'stop');
  const reviews = findings.filter(f => f.level === 'review');
  const notes = findings.filter(f => f.level === 'note');
  const waiting = (S.run.waiting_notes || []).filter(n => n.applies_itself && !n.problem);
  const manual = (S.run.waiting_notes || []).filter(n => !n.applies_itself || n.problem);
  const applied = S.run.applied_notes || [];
  return `
  ${runHeader()}

  ${waiting.length ? `
  <div class="banner notes">
    <div>
      <strong>${plural(waiting.length, 'note is', 'notes are')} waiting for this payroll.</strong>
      <ul class="notelist">
        ${waiting.map(n => `<li>${esc(n.kind_label)} for ${esc(n.caregiver_name || 'someone')}
          ${num(n.amount) ? '— ' + noteAmount(n) : ''}
          ${n.detail ? `<span class="muted">(${esc(n.detail)})</span>` : ''}</li>`).join('')}
      </ul>
    </div>
    <button class="btn btn-primary" onclick="applyNotes()">Add ${waiting.length === 1 ? 'it' : 'them'} to this payroll</button>
  </div>` : ''}

  ${manual.length ? `
  <div class="banner warn">
    <strong>${plural(manual.length, 'note needs', 'notes need')} you to handle ${manual.length === 1 ? 'it' : 'them'} yourself.</strong>
    <ul class="notelist">
      ${manual.map(n => `<li>${esc(n.kind_label)}${n.caregiver_name ? ' for ' + esc(n.caregiver_name) : ''}
        ${n.detail ? `<span class="muted">— ${esc(n.detail)}</span>` : ''}
        ${n.problem ? `<span class="muted">— ${esc(n.problem)}</span>` : ''}</li>`).join('')}
    </ul>
  </div>` : ''}

  ${applied.length ? `<div class="banner good">
    ${plural(applied.length, 'note is', 'notes are')} already in this payroll.
    <a href="#/notes">See them</a>.</div>` : ''}

  <div class="states" style="margin-bottom:22px">
    <button class="state ready" onclick="showCards('ready')">
      <div class="big">${summary.ready}</div>
      <div class="lbl">${summary.ready === 1 ? 'caregiver is' : 'caregivers are'} ready</div>
    </button>
    <button class="state ${summary.needs_review ? 'review' : 'quiet'}" onclick="showCards('needs_review')">
      <div class="big">${summary.needs_review}</div>
      <div class="lbl">need a look from you</div>
    </button>
    <button class="state ${summary.blocked ? 'blocked' : 'quiet'}" onclick="showCards('blocked')">
      <div class="big">${summary.blocked}</div>
      <div class="lbl">can't be paid yet</div>
    </button>
  </div>

  ${stops.length ? `
    <h2>Sort these out first</h2>
    <p class="muted">Payroll can't be finished until these are dealt with.</p>
    <div style="margin-top:12px">${stops.map(findingCard).join('')}</div>` : `
    <div class="banner good">Nothing is blocking this payroll.</div>`}

  ${reviews.length ? `
    <h2 style="margin-top:28px">${plural(reviews.length, 'thing needs', 'things need')} your attention</h2>
    <div style="margin-top:12px">${reviews.map(findingCard).join('')}</div>` : ''}

  ${notes.length ? `
    <h2 style="margin-top:28px">Worth knowing</h2>
    <p class="muted">Nothing to do here — this is the app telling you how it worked things out.</p>
    <div style="margin-top:12px">${notes.map(findingCard).join('')}</div>` : ''}`;
};

const findingCard = (f) => `
  <button class="finding ${f.level}" onclick="openFinding('${esc(f.caregiver_key)}')">
    <div class="ftitle">${esc(f.title)}</div>
    <div class="fdetail">${esc(f.detail)}</div>
    ${f.what_to_do ? `<div class="fdo">${esc(f.what_to_do)}</div>` : ''}
    ${f.booking_ids.length ? `<div class="faint" style="margin-top:7px">
        Booking${f.booking_ids.length > 1 ? 's' : ''}
        ${esc(f.booking_ids.slice(0, 12).join(', '))}${f.booking_ids.length > 12 ? ` and ${f.booking_ids.length - 12} more` : ''}
      </div>` : ''}
  </button>`;

function openFinding(key) {
  if (!key) { showCards('all'); return; }
  S.focusCaregiver = key; S.filter = 'all'; go('cards');
}
function showCards(filter) { S.filter = filter; S.focusCaregiver = null; go('cards'); }

/* =====================================================================
   run header shared by the run screens
   ===================================================================== */
function runHeader() {
  const { run, totals, entered_count, summary } = S.run;
  return `
  <div class="pagehead">
    <div class="eyebrow">Payroll</div>
    <h1>${esc(run.label)}</h1>
    <p class="sub">
      ${plural(totals.caregivers, 'caregiver', 'caregivers')} ·
      ${plural(totals.jobs, 'job', 'jobs')} ·
      ${money(totals.total_paid)} to pay ·
      <strong>${entered_count} of ${totals.caregivers} entered in OnPay</strong>
    </p>
    <div class="progressbar"><i style="width:${totals.caregivers ? (entered_count / totals.caregivers * 100) : 0}%"></i></div>
  </div>
  ${run.locked ? `<div class="banner locked">
      This payroll is finished and locked, so nothing can be changed.
      <button class="btn btn-sm" style="margin-left:10px" onclick="unlockRun()">Unlock it</button>
    </div>` : ''}`;
}

/* =====================================================================
   CAREGIVER CARDS
   ===================================================================== */
VIEWS.cards = () => {
  const { caregivers } = S.run;
  const shown = S.focusCaregiver
    ? caregivers.filter(c => c.key === S.focusCaregiver)
    : (S.filter === 'all' ? caregivers : caregivers.filter(c => c.status === S.filter));
  const label = { all: 'Everyone', ready: 'Ready', needs_review: 'Needing a look', blocked: "Can't be paid yet" };
  return `
  ${runHeader()}
  ${S.focusCaregiver ? `<button class="btn btn-sm btn-ghost" onclick="showCards('all')">← Back to everyone</button>` : `
  <div class="row" style="margin-bottom:16px">
    ${['all', 'ready', 'needs_review', 'blocked'].map(f =>
      `<button class="btn btn-sm ${S.filter === f ? 'btn-primary' : ''}" onclick="showCards('${f}')">${label[f]}</button>`).join('')}
  </div>`}
  ${shown.length ? shown.map(caregiverCard).join('') : `<div class="empty">Nobody in this group.</div>`}`;
};

function caregiverCard(c) {
  const open = S.focusCaregiver === c.key;
  return `
  <div class="card cgcard">
    <button class="cghead" onclick="toggleCard(this)" aria-expanded="${open}">
      <span class="pill ${c.status === 'needs_review' ? 'review' : c.status}">
        ${c.status === 'ready' ? 'Ready' : c.status === 'needs_review' ? 'Needs a look' : "Can't pay yet"}
      </span>
      <span class="name">${esc(c.name || '(no name)')}</span>
      ${c.entered ? '<span class="pill entered">Entered in OnPay</span>' : ''}
      ${c.adjustments.length ? '<span class="pill manual">Manual adjustment</span>' : ''}
      <span class="amount">${money(c.total_paid)}</span>
    </button>
    <div class="cgbody" style="display:${open ? 'block' : 'none'}">
      ${c.findings.length ? c.findings.map(findingCard).join('') : ''}
      ${payLines(c)}
      ${adjustmentBlock(c)}
    </div>
  </div>`;
}

function toggleCard(button) {
  const body = button.nextElementSibling;
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  button.setAttribute('aria-expanded', String(!open));
}

function payLines(c) {
  const lines = [];
  c.tiers.forEach(t => {
    if (num(t.hours) === 0) return;
    lines.push(line(t.label, `${hrs(t.hours)} hrs × $${num(t.rate).toFixed(2)}`, t.pay,
      jobsBehind(c, j => j.tier_key === t.key)));
  });
  if (num(c.guarantee_hours) > 0) {
    lines.push(line('Minimum booking top-up',
      `${hrs(c.guarantee_hours)} hrs paid but not worked — the 4-hour minimum`,
      c.guarantee_pay, jobsBehind(c, j => j.minimum_applied), true));
  }
  if (num(c.ot_hours) > 0) lines.push(line('Overtime', otMath(c), c.ot_premium, overtimeBehind(c)));
  if (num(c.dt_hours) > 0) {
    lines.push(line('Double time',
      `${hrs(c.dt_hours)} hrs past ${S.run.rules.daily_dt_threshold} in a day`,
      c.dt_premium, overtimeBehind(c, true)));
  }
  if (num(c.tips) > 0) lines.push(line('Tips', 'Not Sitterwise wages — entered separately in OnPay',
    c.tips, jobsBehind(c, j => num(j.tip) > 0, j => money(j.tip))));
  if (num(c.bonus) > 0) lines.push(line('Bonuses', 'Taxable', c.bonus,
    jobsBehind(c, j => num(j.bonus) + num(j.lifesaver_bonus) > 0,
      j => money(num(j.bonus) + num(j.lifesaver_bonus)))));
  if (num(c.adjustment_taxable_total) !== 0) {
    lines.push(line('Adjustment', 'Added by hand', c.adjustment_taxable_total, ''));
  }
  lines.push(line('Taxable earnings', 'What OnPay takes tax out of', c.taxable_earnings, '', true));

  const reimb = [];
  if (num(c.mileage_amount) > 0) {
    reimb.push(line('Mileage', `${c.mileage_miles} miles`, c.mileage_amount,
      jobsBehind(c, j => num(j.mileage_amount) > 0,
        j => `${j.mileage_miles} mi × $${num(j.mileage_rate).toFixed(3)} = ${money(j.mileage_amount)}`)));
  }
  if (num(c.other_reimbursement) > 0) {
    reimb.push(line('Other reimbursement', 'Sitterwise records no description',
      c.other_reimbursement, jobsBehind(c, j => num(j.other_reimbursement) > 0,
        j => money(j.other_reimbursement))));
  }
  if (num(c.adjustment_nontaxable_total) !== 0) {
    reimb.push(line('Reimbursement adjustment', 'Added by hand', c.adjustment_nontaxable_total, ''));
  }
  if (reimb.length) reimb.push(line('Reimbursements', 'Not taxable, not wages', c.reimbursements, '', true));

  return `<div class="paylines">${lines.join('')}${reimb.join('')}
    <div class="payline total">
      <div class="plabel">Total being paid</div>
      <div class="pamount">${money(c.total_paid)}</div>
    </div></div>`;
}

const line = (label, math, amount, behind, subtle) => `
  <div class="payline ${subtle ? 'subtle' : ''}">
    <div>
      <div class="plabel">${esc(label)}</div>
      ${math ? `<div class="pmath">${esc(math)}</div>` : ''}
      ${behind || ''}
    </div>
    <div class="pamount">${money(amount)}</div>
  </div>`;

function otMath(c) {
  const week = (c.weeks || []).find(w => num(w.ot_hours) + num(w.weekly_ot_hours) > 0);
  const rate = week ? num(week.regular_rate) : 0;
  return `${hrs(c.ot_hours)} hrs × half of $${rate.toFixed(4)} — the premium on top of straight time`;
}

function jobsBehind(c, test, describe) {
  const jobs = c.jobs.filter(test);
  if (!jobs.length) return '';
  return `<details class="src"><summary>Where this came from (${jobs.length})</summary>
    <div class="tablewrap" style="margin-top:8px">
      <table><thead><tr><th>Date</th><th>Booking</th><th>Client</th><th>Where</th>
        <th class="n">Hours</th><th class="n">${describe ? 'Amount' : 'Pay'}</th></tr></thead><tbody>
      ${jobs.map(j => `<tr>
        <td>${esc(j.workday)}</td><td class="mono">${esc(j.booking_id)}</td>
        <td>${esc(j.client_name)}</td><td>${esc(j.hotel || j.location_type)}</td>
        <td class="n">${hrs(j.hours_worked)}</td>
        <td class="n">${describe ? esc(describe(j)) : money(j.straight_pay)}</td></tr>
        ${j.import_notes.length ? `<tr><td colspan="6" class="faint">${esc(j.import_notes.join(' · '))}</td></tr>` : ''}`).join('')}
      </tbody></table></div></details>`;
}

function overtimeBehind(c, doubleOnly) {
  const rows = [];
  (c.weeks || []).forEach(w => w.days.forEach(d => {
    if (doubleOnly ? num(d.dt_hours) > 0 : num(d.ot_hours) > 0) rows.push({ w, d });
  }));
  if (!rows.length) return '';
  return `<details class="src"><summary>Which days caused it (${rows.length})</summary>
    <div style="margin-top:8px">
    ${[...new Set(rows.map(r => r.w.week_start))].map(ws => {
      const week = rows.find(r => r.w.week_start === ws).w;
      return `<div class="card tight" style="margin-bottom:8px">
        <div class="faint">Week beginning ${esc(week.week_start)}</div>
        <div class="muted" style="font-size:.92rem">${esc(week.regular_rate_explanation)}</div>
        ${rows.filter(r => r.w.week_start === ws).map(r => `
          <div style="margin-top:7px"><strong>${esc(r.d.day)}</strong> — ${esc(r.d.explanation)}
          <div class="faint">Bookings ${esc(r.d.booking_ids.join(', '))}</div></div>`).join('')}
      </div>`;
    }).join('')}
    </div></details>`;
}

function adjustmentBlock(c) {
  const locked = S.run.run.locked;
  return `
  <div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--border)">
    ${c.adjustments.map(a => `
      <div class="row" style="margin-bottom:8px">
        <span class="pill manual">Manual adjustment</span>
        <span>${esc(a.kind)}${a.booking_id ? ` on booking ${esc(a.booking_id)}` : ''}:
          <s class="muted">${esc(a.original_value)}</s> → <strong>${esc(a.new_value)}</strong></span>
        <span class="faint">${esc(a.reason)} · ${esc((a.created_at || '').slice(0, 16).replace('T', ' '))}</span>
        ${locked ? '' : `<button class="btn btn-sm btn-ghost" onclick="removeAdjustment('${a.id}')">Undo</button>`}
      </div>`).join('')}
    ${locked ? '' : `<button class="btn btn-sm" onclick="openAdjust('${esc(c.key)}')">Correct something</button>`}
  </div>`;
}

/* ---------- manual adjustment dialog ---------- */
function openAdjust(key) {
  const c = S.run.caregivers.find(x => x.key === key);
  const box = document.createElement('div');
  box.className = 'modalbg';
  box.onclick = (e) => { if (e.target === box) box.remove(); };
  box.innerHTML = `<div class="modal">
    <h2>Correct something for ${esc(c.name)}</h2>
    <p class="muted">The imported figure is kept. Everything you change is marked and dated.</p>
    <label class="field"><span>What are you changing?</span>
      <select id="adjkind" onchange="adjKindChanged()">
        <option value="hours">Hours on a job</option>
        <option value="rate">Pay rate on a job</option>
        <option value="tip">Tip on a job</option>
        <option value="mileage">Mileage on a job</option>
        <option value="reimbursement">Other reimbursement on a job</option>
        <option value="adjustment">A one-off amount for this caregiver</option>
      </select></label>
    <label class="field" id="adjbookingwrap"><span>Which job?</span>
      <select id="adjbooking" onchange="adjBookingChanged()">
        ${c.jobs.map(j => `<option value="${esc(j.booking_id)}">
          ${esc(j.workday)} · ${esc(j.client_name)} · ${hrs(j.hours_worked)} hrs · ${money(j.straight_pay)}
        </option>`).join('')}
      </select></label>
    <label class="field"><span>New value</span>
      <input type="number" step="0.01" id="adjvalue"></label>
    <label class="field" id="adjtaxwrap" style="display:none"><span>Is it taxable pay?</span>
      <select id="adjtaxable">
        <option value="1">Yes — it's wages</option>
        <option value="0">No — it's a reimbursement</option>
      </select></label>
    <label class="field"><span>Why? This goes on the record.</span>
      <input type="text" id="adjreason" placeholder="e.g. Family confirmed a $40 cash tip"></label>
    <div class="row" style="justify-content:flex-end;margin-top:6px">
      <button class="btn btn-ghost" onclick="this.closest('.modalbg').remove()">Cancel</button>
      <button class="btn btn-primary" onclick="saveAdjust('${esc(key)}')">Save the change</button>
    </div>
  </div>`;
  document.body.appendChild(box);
  adjBookingChanged();
}

function adjKindChanged() {
  const kind = $('#adjkind').value;
  $('#adjbookingwrap').style.display = kind === 'adjustment' ? 'none' : 'block';
  $('#adjtaxwrap').style.display = kind === 'adjustment' ? 'block' : 'none';
  adjBookingChanged();
}

function adjBookingChanged() {
  const kind = $('#adjkind').value;
  if (kind === 'adjustment') return;
  const c = S.run.caregivers.find(x => x.key === S.adjustKey) ||
            S.run.caregivers.find(x => x.jobs.some(j => j.booking_id === $('#adjbooking').value));
  const job = c && c.jobs.find(j => j.booking_id === $('#adjbooking').value);
  if (!job) return;
  const current = { hours: job.hours_worked, rate: job.rate, tip: job.tip,
                    mileage: job.mileage_amount, reimbursement: job.other_reimbursement }[kind];
  $('#adjvalue').value = current;
  $('#adjvalue').dataset.original = current;
}

async function saveAdjust(key) {
  const kind = $('#adjkind').value;
  try {
    await post(`/api/runs/${S.runId}/adjustments`, {
      caregiver_key: key, kind,
      booking_id: kind === 'adjustment' ? '' : $('#adjbooking').value,
      original_value: $('#adjvalue').dataset.original || '0',
      new_value: $('#adjvalue').value,
      reason: $('#adjreason').value,
      taxable: kind !== 'adjustment' || $('#adjtaxable').value === '1',
    });
    document.querySelector('.modalbg').remove();
    toast('Change saved and marked');
    S.focusCaregiver = key;
    await refreshRun();
  } catch (e) { toast(e.message, true); }
}

async function removeAdjustment(id) {
  try {
    await del(`/api/runs/${S.runId}/adjustments/${id}`);
    toast('Change undone'); await refreshRun();
  } catch (e) { toast(e.message, true); }
}

/* =====================================================================
   ENTER IN ONPAY — the grid
   ===================================================================== */
VIEWS.onpay = () => {
  const { caregivers, rules, totals, entered_count } = S.run;
  const tiers = rules.tiers;
  return `
  ${runHeader()}
  <div class="row" style="margin-bottom:14px">
    <button class="btn btn-primary" onclick="startEntry()">Go through them one at a time</button>
    <a class="btn" href="/api/runs/${S.runId}/export/onpay_entry">Download this as a spreadsheet</a>
    <span class="muted" style="margin-left:auto">${entered_count} of ${totals.caregivers} done</span>
  </div>
  <div class="tablewrap tall">
    <table><thead><tr>
      <th></th><th>Caregiver</th>
      ${tiers.map(t => `<th class="n">${esc(t.label)}<br><span class="faint">$${num(t.rate).toFixed(2)}</span></th>`).join('')}
      <th class="n">Min hrs</th><th class="n">OT hrs</th><th class="n">OT $</th>
      <th class="n">DT hrs</th><th class="n">DT $</th>
      <th class="n">Tips</th><th class="n">Bonus</th><th class="n">Mileage</th><th class="n">Reimb.</th>
      <th class="n">Total</th>
    </tr></thead><tbody>
    ${caregivers.map(c => `<tr>
      <td><input type="checkbox" ${c.entered ? 'checked' : ''} ${S.run.run.locked ? 'disabled' : ''}
           onchange="markEntered('${esc(c.key)}', this.checked)"></td>
      <td><strong>${esc(c.name)}</strong>
        ${c.status === 'blocked' ? '<span class="pill blocked">Can\'t pay yet</span>' : ''}
        ${c.adjustments.length ? '<span class="pill manual">Adjusted</span>' : ''}</td>
      ${tiers.map(t => cell(c.tiers.find(x => x.key === t.key)?.hours, hrs)).join('')}
      ${cell(c.guarantee_hours, hrs)}${cell(c.ot_hours, hrs)}${cell(c.ot_premium, money)}
      ${cell(c.dt_hours, hrs)}${cell(c.dt_premium, money)}
      ${cell(c.tips, money)}${cell(c.bonus, money)}
      ${cell(c.mileage_amount, money)}${cell(c.other_reimbursement, money)}
      <td class="n"><strong>${money(c.total_paid)}</strong></td>
    </tr>`).join('')}
    </tbody>
    <tfoot><tr>
      <td></td><th>Everyone</th>
      ${tiers.map(t => `<td class="n"><strong>${hrs((totals.tiers.find(x => x.key === t.key) || {}).hours)}</strong></td>`).join('')}
      <td class="n"><strong>${hrs(totals.guarantee_hours)}</strong></td>
      <td class="n"><strong>${hrs(totals.ot_hours)}</strong></td>
      <td class="n"><strong>${money(totals.ot_premium)}</strong></td>
      <td class="n"><strong>${hrs(totals.dt_hours)}</strong></td>
      <td class="n"><strong>${money(totals.dt_premium)}</strong></td>
      <td class="n"><strong>${money(totals.tips)}</strong></td>
      <td class="n"><strong>${money(totals.bonus)}</strong></td>
      <td class="n"><strong>${money(totals.mileage_amount)}</strong></td>
      <td class="n"><strong>${money(totals.other_reimbursement)}</strong></td>
      <td class="n"><strong>${money(totals.total_paid)}</strong></td>
    </tr></tfoot></table>
  </div>`;
};

const cell = (value, fmt) => num(value) === 0
  ? '<td class="n zero">—</td>' : `<td class="n">${fmt(value)}</td>`;

async function markEntered(key, entered) {
  try {
    await post(`/api/runs/${S.runId}/entered`, { caregiver_key: key, entered });
    await refreshRun();
  } catch (e) { toast(e.message, true); await refreshRun(); }
}

/* =====================================================================
   ENTER IN ONPAY — one at a time
   ===================================================================== */
function startEntry() {
  const first = S.run.caregivers.findIndex(c => !c.entered);
  S.entryIndex = first === -1 ? 0 : first;
  go('entry');
}

VIEWS.entry = () => {
  const list = S.run.caregivers;
  if (!list.length) return `<div class="empty">Nobody to enter.</div>`;
  S.entryIndex = Math.max(0, Math.min(S.entryIndex, list.length - 1));
  const c = list[S.entryIndex];
  const tiers = S.run.rules.tiers;
  const rows = [];
  tiers.forEach(t => {
    const mine = c.tiers.find(x => x.key === t.key);
    rows.push(bigNum(t.label.toUpperCase() + ' HOURS', mine ? hrs(mine.hours) : '0', num(mine?.hours) > 0));
  });
  if (num(c.guarantee_hours) > 0) rows.push(bigNum('MINIMUM TOP-UP HOURS', hrs(c.guarantee_hours), true));
  rows.push(bigNum('OVERTIME HOURS', hrs(c.ot_hours), num(c.ot_hours) > 0));
  if (num(c.ot_premium) > 0) rows.push(bigNum('OVERTIME PREMIUM $', num(c.ot_premium).toFixed(2), true));
  if (num(c.dt_hours) > 0) {
    rows.push(bigNum('DOUBLE TIME HOURS', hrs(c.dt_hours), true));
    rows.push(bigNum('DOUBLE TIME PREMIUM $', num(c.dt_premium).toFixed(2), true));
  }
  rows.push(bigNum('TIPS', num(c.tips).toFixed(2), num(c.tips) > 0));
  if (num(c.bonus) > 0) rows.push(bigNum('BONUS', num(c.bonus).toFixed(2), true));
  rows.push(bigNum('MILEAGE', num(c.mileage_amount).toFixed(2), num(c.mileage_amount) > 0));
  rows.push(bigNum('REIMBURSEMENT', num(c.other_reimbursement).toFixed(2), num(c.other_reimbursement) > 0));

  const mixedWarning = c.uses_multiple_rates && (num(c.ot_hours) > 0 || num(c.dt_hours) > 0);
  return `
  <div class="entry">
    <div class="pagehead entryhead">
      <div class="who">${esc(c.name)}</div>
      <div class="of">${S.entryIndex + 1} of ${S.run.caregivers.length}
        ${c.entered ? '· <span class="pill entered">Already entered</span>' : ''}</div>
    </div>
    ${c.status === 'blocked' ? `<div class="banner bad">This caregiver can't be paid yet — see the payroll check.</div>` : ''}
    ${mixedWarning ? `<div class="banner warn">
      ${esc(c.name)} worked at two different rates this period, so OnPay won't calculate their
      overtime correctly on its own. Enter the overtime premium as a dollar amount.</div>` : ''}
    ${c.adjustments.length ? `<div class="banner warn">
      ${plural(c.adjustments.length, 'figure was', 'figures were')} corrected by hand for this caregiver.</div>` : ''}

    <div class="card" style="padding:0">${rows.join('')}</div>

    ${(c.onpay_lines || []).length ? `
    <div class="card tight" style="margin-top:14px">
      <h3 style="margin:0 0 4px">The lines to make in OnPay</h3>
      <p class="muted" style="margin:0 0 4px;font-size:13.5px">Exactly what the import file
        does, so typing it by hand comes to the same thing. The note is so
        ${esc(c.name.split(' ')[0])} can see what she is being paid for — OnPay's import
        file has no room for it, so it is typed onto the line.</p>
      ${num(c.ot_hours) > 0 || num(c.dt_hours) > 0 ? `
      <p class="muted" style="margin:0 0 12px;font-size:13px">
        Regular reads ${hrs(num(c.hours_worked) - num(c.ot_hours) - num(c.dt_hours)
          + num(c.guarantee_hours))} here rather than ${hrs(c.hours_worked)}, because OnPay
        wants the overtime hours on their own line rather than counted twice.</p>` : ''}
      ${c.onpay_lines.map(l => `
        <div class="payline">
          <div class="payline-what">
            <strong>${esc(l.name)}</strong>
            <span class="faint">${l.hours ? hrs(l.hours) + 'h @ $' + num(l.rate).toFixed(2)
              + ' · ' : ''}${money(l.amount)}</span>
          </div>
          <div class="payline-note">${l.note ? esc(l.note) : '<span class="faint">no note</span>'}</div>
          ${l.note ? `<button class="btn btn-sm" onclick="copy(${JSON.stringify(l.note)
            .replace(/"/g, '&quot;')}, this)">Copy</button>` : '<span></span>'}
        </div>`).join('')}
    </div>` : ''}

    <div class="card tight" style="margin-top:14px">
      <div class="spread"><span class="muted">Total being paid</span>
        <strong style="font-size:1.4rem">${money(c.total_paid)}</strong></div>
    </div>

    <div class="row" style="margin-top:18px">
      <button class="btn" onclick="entryMove(-1)" ${S.entryIndex === 0 ? 'disabled' : ''}>← Previous</button>
      <button class="btn btn-primary btn-huge" style="flex:1"
        onclick="entryDone('${esc(c.key)}')" ${S.run.run.locked ? 'disabled' : ''}>
        ${c.entered ? 'Next caregiver →' : '✓ Mark entered in OnPay'}
      </button>
      <button class="btn" onclick="entryMove(1)"
        ${S.entryIndex >= S.run.caregivers.length - 1 ? 'disabled' : ''}>Skip →</button>
    </div>
    <div class="row" style="margin-top:12px;justify-content:center">
      <button class="btn btn-ghost btn-sm" onclick="go('onpay')">Back to the whole list</button>
    </div>
  </div>`;
};

const bigNum = (key, value, present) => `
  <div class="bignum">
    <div class="k">${esc(key)}</div>
    <div class="v ${present ? '' : 'none'}">${present ? esc(value) : '—'}</div>
    ${present ? `<button class="btn btn-sm copy" onclick="copy('${esc(value)}', this)">Copy</button>` : ''}
  </div>`;

function entryMove(step) { S.entryIndex += step; render(); }

async function entryDone(key) {
  const c = S.run.caregivers[S.entryIndex];
  try {
    if (!c.entered) await post(`/api/runs/${S.runId}/entered`, { caregiver_key: key, entered: true });
    await loadRun();
    const next = S.run.caregivers.findIndex((x, i) => i > S.entryIndex && !x.entered);
    S.entryIndex = next === -1 ? Math.min(S.entryIndex + 1, S.run.caregivers.length - 1) : next;
    await render();
  } catch (e) { toast(e.message, true); }
}

/* =====================================================================
   RECONCILE
   ===================================================================== */
VIEWS.reconcile = () => {
  const { totals, reconciliation: r, summary, run } = S.run;
  const diffs = r.pay_differences;
  return `
  ${runHeader()}
  <div class="card">
    <h2>What this payroll comes to</h2>
    <div class="paylines" style="margin-top:10px">
      ${line('Caregivers', '', 0, '', true).replace(money(0), totals.caregivers)}
      ${totals.tiers.map(t => line(`${t.label} hours`, `at $${num(t.rate).toFixed(2)}`, 0, '', true)
          .replace(money(0), hrs(t.hours))).join('')}
      ${line('Overtime hours', '', 0, '', true).replace(money(0), hrs(totals.ot_hours))}
      ${line('Double time hours', '', 0, '', true).replace(money(0), hrs(totals.dt_hours))}
    </div>
    <div class="paylines" style="margin-top:18px">
      ${totals.tiers.map(t => line(`${t.label} wages`, '', t.pay)).join('')}
      ${num(totals.guarantee_pay) > 0 ? line('Minimum booking top-up', `${hrs(totals.guarantee_hours)} hrs`, totals.guarantee_pay) : ''}
      ${line('Overtime premium', '', totals.ot_premium)}
      ${num(totals.dt_premium) > 0 ? line('Double time premium', '', totals.dt_premium) : ''}
      ${line('Tips', 'Not Sitterwise wages', totals.tips)}
      ${num(totals.bonus) > 0 ? line('Bonuses', '', totals.bonus) : ''}
      ${line('Taxable earnings', '', totals.taxable_earnings, '', true)}
      ${line('Mileage', `${totals.mileage_miles} miles`, totals.mileage_amount)}
      ${line('Other reimbursements', '', totals.other_reimbursement)}
      <div class="payline total"><div class="plabel">Expected total employee payments</div>
        <div class="pamount">${money(totals.total_paid)}</div></div>
    </div>
  </div>

  <div class="card">
    <h2>Nothing went missing</h2>
    <div class="paylines">
      ${line('Rows in the Sitterwise file', '', 0, '', true).replace(money(0), r.source_rows)}
      ${line('Jobs dated in this pay period', '', 0, '', true).replace(money(0), r.jobs_in_period)}
      ${line('Jobs being paid', '', 0, '', true).replace(money(0), r.jobs_paid)}
      ${line('Jobs accounted for in payroll', '', 0, '', true).replace(money(0), r.jobs_accounted_for)}
      ${Object.entries(r.exclusions).map(([why, count]) =>
        line(`Left out — ${why}`, '', 0, '', true).replace(money(0), count)).join('')}
    </div>
    <div class="banner ${r.balances ? 'good' : 'bad'}" style="margin-top:14px">
      ${r.balances
        ? `Every one of the ${r.jobs_in_period} jobs in this period is either being paid or explained above.`
        : `These do not add up. Do not finalise this payroll — check the exports.`}
    </div>
  </div>

  <div class="card">
    <h2>Against what Sitterwise says</h2>
    <p class="muted">Sitterwise works out its own figure for each job. The app recalculates from
      hours and rate. They should match.</p>
    <div class="paylines">
      ${line('Sitterwise total for these jobs', '', r.exported_pay_total)}
      ${line('What the app calculates', 'Straight time and minimum top-up only', r.app_straight_total)}
      ${line('Difference', '', r.pay_difference, '', true)}
    </div>
    ${diffs.length ? `
      <h3 style="margin-top:16px">${plural(diffs.length, 'job disagrees', 'jobs disagree')}</h3>
      <div class="tablewrap" style="margin-top:8px"><table>
        <thead><tr><th>Booking</th><th>Caregiver</th><th>Date</th><th class="n">Sitterwise</th>
          <th class="n">App</th><th class="n">Difference</th><th>Why</th></tr></thead><tbody>
        ${diffs.map(d => `<tr><td class="mono">${esc(d.booking_id)}</td><td>${esc(d.caregiver)}</td>
          <td>${esc(d.date)}</td><td class="n">${money(d.sitterwise_says)}</td>
          <td class="n">${money(d.app_calculates)}</td><td class="n">${money(d.difference)}</td>
          <td style="white-space:normal">${esc(d.why)}</td></tr>`).join('')}
      </tbody></table></div>` :
      `<div class="banner good">Every job matches what Sitterwise says.</div>`}
  </div>

  ${run.locked ? '' : `
  <div class="card">
    <h2>Finish this payroll</h2>
    <p class="muted">Once finished it becomes read-only, and these jobs can't be paid again in a
      later run. You can unlock it if you need to.</p>
    ${summary.can_finalize
      ? `<button class="btn btn-primary btn-huge" onclick="finalizeRun()">Finish payroll for ${esc(run.label)}</button>`
      : `<div class="banner bad">${plural(summary.blocked, 'caregiver', 'caregivers')} still
           can't be paid. <a href="#/check">Sort that out first</a>.</div>`}
  </div>`}`;
};

async function finalizeRun() {
  if (!confirm('Finish this payroll and lock it?')) return;
  try { await post(`/api/runs/${S.runId}/finalize`, {}); toast('Payroll finished'); await refreshRun(); }
  catch (e) { toast(e.message, true); }
}

async function unlockRun() {
  const reason = prompt('Why are you unlocking this payroll? It goes on the record.');
  if (reason === null) return;
  try { await post(`/api/runs/${S.runId}/unlock`, { reason }); toast('Unlocked'); await refreshRun(); }
  catch (e) { toast(e.message, true); }
}

/* =====================================================================
   EXPORTS
   ===================================================================== */
VIEWS.exports = () => `
  ${runHeader()}
  <div id="exportlist"><div class="empty">Loading…</div></div>`;

VIEWS.after_exports = async () => {
  const data = await api(`/api/runs/${S.runId}/exports`);
  $('#exportlist').innerHTML = data.exports.map(e => `
    <div class="card">
      <div class="spread">
        <div>
          <h3>${esc(e.name)}</h3>
          <p class="muted" style="margin:4px 0 0">${esc(e.description)}</p>
          <div class="faint mono" style="margin-top:6px">${esc(e.filename)}</div>
        </div>
        <a class="btn btn-primary" href="/api/runs/${S.runId}/export/${e.key}">Download</a>
      </div>
      ${(e.skipped && e.skipped.length) ? `<div class="banner warn" style="margin-top:12px">
        Left out for having no OnPay Clock User set: ${esc(e.skipped.join(', '))}.
        Add it on the <a href="#/roster">roster</a>, or enter these by hand.</div>` : ''}
    </div>`).join('');
};

/* =====================================================================
   ROSTER
   ===================================================================== */
VIEWS.roster = () => {
  const list = S.roster.roster;
  const statuses = S.roster.statuses;
  const attention = list.filter(e => e.needs_attention);
  return `
  <div class="pagehead">
    <h1>Caregiver roster</h1>
    <p class="sub">Who is actually set up to be paid in OnPay.</p>
  </div>

  <div class="card">
    <div class="spread">
      <div>
        <h3>Import from OnPay</h3>
        <p class="muted" style="margin:4px 0 0">Export your employee list out of OnPay and drop it
          here. Better than keeping the same information in two places.</p>
      </div>
      <button class="btn btn-primary" onclick="$('#rosterfile').click()">Import employee list</button>
      <input type="file" id="rosterfile" accept=".csv,.xlsx" hidden onchange="importRoster(this)">
    </div>
    <div id="rosterimport"></div>
  </div>

  ${attention.length ? `<div class="banner warn">
    ${plural(attention.length, 'caregiver is', 'caregivers are')} not fully set up:
    ${esc(attention.map(e => e.display_name).join(', '))}.</div>` : ''}

  <div class="tablewrap tall">
    <table><thead><tr><th>Caregiver</th><th>OnPay status</th><th>Clock User</th>
      <th>Employee ID</th><th>Name in OnPay</th><th>Note</th><th></th></tr></thead><tbody>
    ${list.map(e => `<tr>
      <td><strong>${esc(e.display_name)}</strong></td>
      <td><select onchange="saveRoster('${esc(e.caregiver_key)}', this)" data-f="status">
        ${statuses.map(s => `<option value="${s.key}" ${s.key === e.status ? 'selected' : ''}>${esc(s.label)}</option>`).join('')}
      </select></td>
      <td><input type="text" value="${esc(e.onpay_clock_user)}" data-f="onpay_clock_user"
           onchange="saveRoster('${esc(e.caregiver_key)}', this)" style="min-width:130px"></td>
      <td><input type="text" value="${esc(e.onpay_employee_id)}" data-f="onpay_employee_id"
           onchange="saveRoster('${esc(e.caregiver_key)}', this)" style="min-width:110px"></td>
      <td><input type="text" value="${esc(e.onpay_name || '')}" data-f="onpay_name"
           placeholder="only if different"
           onchange="saveRoster('${esc(e.caregiver_key)}', this)" style="min-width:150px"></td>
      <td><input type="text" value="${esc(e.note)}" data-f="note"
           onchange="saveRoster('${esc(e.caregiver_key)}', this)" style="min-width:170px"></td>
      <td class="faint">${esc((e.updated_at || '').slice(0, 10))}</td>
    </tr>`).join('')}
    </tbody></table>
  </div>`;
};

async function saveRoster(key, input) {
  const row = input.closest('tr');
  const value = (f) => { const el = row.querySelector(`[data-f="${f}"]`); return el ? el.value : ''; };
  const entry = S.roster.roster.find(e => e.caregiver_key === key);
  try {
    await post('/api/roster', {
      caregiver_key: key, display_name: entry.display_name,
      status: value('status'), onpay_clock_user: value('onpay_clock_user'),
      onpay_employee_id: value('onpay_employee_id'),
      onpay_name: value('onpay_name'), note: value('note'),
    });
    toast('Saved');
    S.roster = await api('/api/roster');
  } catch (e) { toast(e.message, true); }
}

async function importRoster(input) {
  const file = input.files[0];
  if (!file) return;
  try {
    const res = await api('/api/roster/import', {
      method: 'POST', headers: { 'X-Filename': encodeURIComponent(file.name) }, body: file,
    });
    S.roster = await api('/api/roster');
    await render();
    $('#rosterimport').innerHTML = `
      <div class="banner good" style="margin-top:12px">
        ${res.added} added, ${res.updated} updated from OnPay.</div>
      ${res.problems.map(p => `<div class="banner warn">${esc(p)}</div>`).join('')}
      ${res.not_in_onpay_file.length ? `<div class="banner warn">
        On the roster here but not in the OnPay file:
        ${esc(res.not_in_onpay_file.join(', '))}.</div>` : ''}`;
  } catch (e) { toast(e.message, true); }
}

/* =====================================================================
   HISTORY
   ===================================================================== */
VIEWS.history = () => {
  const runs = S.home.runs;
  return `
  <div class="pagehead">
    <h1>Past payrolls</h1>
    <p class="sub">Finished payrolls are read-only until you deliberately unlock them.</p>
  </div>
  ${runs.length ? `<div class="card" style="padding:0">${runs.map(r => `
    <button class="runrow" onclick="openRun('${r.id}')">
      <div>
        <div class="rl">${esc(r.label)}</div>
        <div class="faint">${esc(r.source_filename || '')} ·
          ${r.finalized_at ? 'finished ' + esc(r.finalized_at.slice(0, 10)) : 'started ' + esc((r.created_at || '').slice(0, 10))}
          · rules ${esc(r.rules_version || '')}</div>
      </div>
      <span class="pill ${r.status === 'finalized' ? 'entered' : 'plain'}" style="margin-left:auto">
        ${r.status === 'finalized' ? 'Locked' : 'In progress'}</span>
    </button>`).join('')}</div>` : `<div class="empty">No payrolls yet.</div>`}

  <h2 style="margin-top:30px">Everything that's been changed by hand</h2>
  <div id="audit" class="card"><p class="muted">Loading…</p></div>`;
};

VIEWS.after_history = async () => {
  const data = await api('/api/audit');
  $('#audit').innerHTML = data.entries.length ? `<div class="tablewrap"><table>
    <thead><tr><th>When</th><th>What</th><th>Detail</th></tr></thead><tbody>
    ${data.entries.map(e => `<tr><td class="mono">${esc(e.at.slice(0, 16).replace('T', ' '))}</td>
      <td>${esc(e.action.replace(/_/g, ' '))}</td>
      <td style="white-space:normal">${esc(e.detail || '')}</td></tr>`).join('')}
  </tbody></table></div>` : '<p class="muted">Nothing yet.</p>';
};

/* =====================================================================
   SETTINGS
   ===================================================================== */
VIEWS.settings = () => {
  const rules = S.settings.rules;
  const ot = rules.overtime || {};
  return `
  <div class="pagehead">
    <h1>Settings</h1>
    <p class="sub">Payroll rules live here, not in the code. Changing them changes future
      payrolls — payrolls you've already finished keep the rules they were run with.</p>
  </div>

  <div class="card">
    <h2>Pay rates</h2>
    ${(rules.pay_rates.tiers || []).map((t, i) => `
      <label class="field"><span>${esc(t.label)}</span>
        <input type="number" step="0.01" value="${t.rate}" data-tier="${i}"></label>`).join('')}
    <label class="field"><span>Minimum hours paid per booking</span>
      <input type="number" step="0.25" id="minhours" value="${rules.minimum_booking.minimum_hours}"></label>
  </div>

  ${recurringSection()}

  <div class="card">
    <h2>Overtime</h2>
    <div class="banner locked">California 8/40 with double time — decided 22 August 2026.
      Time and a half over 8 hours in a day, double time over 12, and seventh-consecutive-day
      rules. Everything here is a setting, so it can be changed later without rebuilding anything.</div>
    <label class="field"><span>Overtime after this many hours in a day</span>
      <input type="number" step="0.5" id="dailyot" value="${ot.daily_overtime.threshold_hours}"></label>
    <label class="field"><span>Double time after this many hours in a day</span>
      <input type="number" step="0.5" id="dailydt" value="${ot.daily_double_time.threshold_hours}"></label>
    <label class="field"><span>Pay double time at all?</span>
      <select id="dtenabled">
        <option value="1" ${ot.daily_double_time.enabled ? 'selected' : ''}>Yes</option>
        <option value="0" ${!ot.daily_double_time.enabled ? 'selected' : ''}>No</option></select></label>
    <label class="field"><span>Pay weekly overtime?</span>
      <select id="weeklyenabled">
        <option value="1" ${ot.weekly_overtime.enabled ? 'selected' : ''}>Yes</option>
        <option value="0" ${!ot.weekly_overtime.enabled ? 'selected' : ''}>No — daily overtime only</option>
      </select></label>
    <label class="field"><span>Weekly overtime after this many hours</span>
      <input type="number" step="1" id="weeklyot" value="${ot.weekly_overtime.threshold_hours}"></label>
    <label class="field"><span>The work week starts on</span>
      <select id="wwstart">
        ${['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'].map(d =>
          `<option value="${d}" ${ot.workweek_start_day === d ? 'selected' : ''}>${d[0].toUpperCase() + d.slice(1)}</option>`).join('')}
      </select></label>
    <label class="field"><span>Confirmed that's right?</span>
      <select id="wwconfirmed">
        <option value="0" ${!ot.workweek_start_confirmed ? 'selected' : ''}>Not yet</option>
        <option value="1" ${ot.workweek_start_confirmed ? 'selected' : ''}>Yes, confirmed</option>
      </select></label>
  </div>

  <div class="card">
    <h2>Mileage</h2>
    <p class="muted">The IRS rate changes, sometimes mid-year, so it's a list of dates rather than
      one number. The app uses whichever was in force on the day of the job.</p>
    <div class="tablewrap"><table><thead><tr><th>From</th><th class="n">Per mile</th><th>Where it's from</th></tr></thead>
      <tbody>${rules.reimbursements.mileage.rates_by_effective_date.map(r =>
        `<tr><td>${esc(r.effective)}</td><td class="n">$${r.rate}</td><td>${esc(r.source || '')}</td></tr>`).join('')}
      </tbody></table></div>
  </div>

  <div class="row"><button class="btn btn-primary" onclick="saveSettings()">Save settings</button>
    <span class="muted">Rules file: <span class="mono">${esc(S.settings.path)}</span></span></div>

  <div class="card" style="margin-top:24px">
    <h2>Everything else</h2>
    <p class="muted">The full rules file. Anything not on this page can be changed here.</p>
    <textarea id="rawrules" style="min-height:280px">${esc(JSON.stringify(rules, null, 2))}</textarea>
    <button class="btn" style="margin-top:10px" onclick="saveRawSettings()">Save the file as written</button>
  </div>

  <div class="card">
    <h2>OnPay import columns</h2>
    <p class="muted">OnPay doesn't publish its CSV format and has to switch the import on for your
      account. Ask their support for the column names, then set them here.</p>
    <textarea id="rawmapping" style="min-height:200px">${esc(JSON.stringify(S.settings.onpay_mapping, null, 2))}</textarea>
    <button class="btn" style="margin-top:10px" onclick="saveMapping()">Save column mapping</button>
  </div>`;
};

async function saveSettings() {
  const rules = JSON.parse(JSON.stringify(S.settings.rules));
  document.querySelectorAll('[data-tier]').forEach(input => {
    rules.pay_rates.tiers[Number(input.dataset.tier)].rate = Number(input.value);
  });
  rules.minimum_booking.minimum_hours = Number($('#minhours').value);
  const ot = rules.overtime;
  ot.daily_overtime.threshold_hours = Number($('#dailyot').value);
  ot.daily_double_time.threshold_hours = Number($('#dailydt').value);
  ot.daily_double_time.enabled = $('#dtenabled').value === '1';
  ot.weekly_overtime.enabled = $('#weeklyenabled').value === '1';
  ot.weekly_overtime.threshold_hours = Number($('#weeklyot').value);
  ot.workweek_start_day = $('#wwstart').value;
  ot.workweek_start_confirmed = $('#wwconfirmed').value === '1';
  await pushSettings({ rules });
}

async function saveRawSettings() {
  try { await pushSettings({ rules: JSON.parse($('#rawrules').value) }); }
  catch (e) { toast('That is not valid JSON: ' + e.message, true); }
}

async function saveMapping() {
  try { await pushSettings({ onpay_mapping: JSON.parse($('#rawmapping').value) }); }
  catch (e) { toast('That is not valid JSON: ' + e.message, true); }
}

async function pushSettings(payload) {
  try {
    await post('/api/settings', payload);
    toast('Settings saved');
    S.settings = await api('/api/settings');
    S.run = null;
    await render();
  } catch (e) { toast(e.message, true); }
}

/* ---------- go ---------- */
route();

/* =====================================================================
   NOTES — the old "Payroll Odds & Ends" sheet, without the retyping
   ===================================================================== */
const NOTE_HELP = {
  bonus: 'Extra pay on top of the work. Taxable.',
  cancellation: 'Paid because a job fell through. Taxable.',
  extra_pay: 'Any other pay owed. Taxable.',
  dock: 'Take money off. Enter it as a positive number.',
  reimbursement: 'Paying somebody back - Trustline, parking. Not taxable, and kept out of overtime.',
  mileage: 'Mileage owed that the export missed. Not taxable.',
  hours: 'The hours on a booking were wrong. Needs the booking number.',
  rate: 'The rate on a booking was wrong. Needs the booking number.',
  exclude: 'Already paid another way. The app will show this but not act on it.',
  check: 'Pay by paper check. The app will show this but not act on it.',
  other: 'Just something to remember.',
};

VIEWS.notes = () => {
  const all = (S.notes && S.notes.notes) || [];
  const kinds = (S.notes && S.notes.kinds) || [];
  const open = all.filter(n => n.status === 'open');
  const done = all.filter(n => n.status !== 'open');
  return `
  <div class="pagehead">
    <h1>Payroll notes</h1>
    <p class="sub">Anything to remember on a payroll. Write it down when you notice it;
      the payroll it belongs to picks it up.</p>
  </div>

  <div class="card">
    <h3>Add a note</h3>
    <div class="noteform">
      <label>What kind
        <select id="nkind" onchange="noteKindChanged()">
          ${kinds.map(k => `<option value="${k.key}">${esc(k.label)}</option>`).join('')}
        </select>
      </label>
      <label>Who is it about
        <input type="text" id="nname" placeholder="Caregiver name" list="rosternames">
      </label>
      <label id="namountwrap">How much
        <input type="text" id="namount" placeholder="50.00" inputmode="decimal">
      </label>
      <label id="nbookingwrap" hidden>Booking number
        <input type="text" id="nbooking" placeholder="14595">
      </label>
      <label>Which payroll
        <select id="napplies" onchange="noteKindChanged()">
          <option value="next">The next one</option>
          <option value="pick">A particular week…</option>
        </select>
      </label>
      <label id="ndatewrap" hidden>Any date in that week
        <input type="date" id="ndate">
      </label>
    </div>
    <label style="display:block;margin-top:12px">Why
      <input type="text" id="ndetail" placeholder="Last minute cancellation by the client"
             style="width:100%" onkeydown="if(event.key==='Enter')addNote()">
    </label>
    <p class="muted" id="nhelp" style="margin:10px 0 0">${esc(NOTE_HELP.bonus)}</p>
    <div style="margin-top:14px"><button class="btn btn-primary" onclick="addNote()">Add note</button></div>
  </div>

  <h2 style="margin-top:30px">Waiting (${open.length})</h2>
  ${open.length ? `<div class="card" style="padding:0">
    ${open.map(noteRow).join('')}
  </div>` : '<p class="muted">Nothing waiting. Everything is accounted for.</p>'}

  ${done.length ? `<h2 style="margin-top:30px">Already done (${done.length})</h2>
  <div class="card" style="padding:0">${done.slice(0, 40).map(noteRow).join('')}</div>` : ''}`;
};

const noteAmount = (n) =>
  n.kind === 'hours' ? `${hrs(n.amount)} hours`
  : n.kind === 'rate' ? `${money(n.amount)}/hour`
  : money(n.amount);

const noteRow = (n) => `
  <div class="noterow ${n.status !== 'open' ? 'is-done' : ''}">
    <div class="notemain">
      <div><strong>${esc(n.kind_label)}</strong>
        ${n.caregiver_name ? ` &middot; ${esc(n.caregiver_name)}` : ''}
        ${num(n.amount) ? ` &middot; ${noteAmount(n)}` : ''}
        ${n.booking_id ? ` &middot; booking ${esc(n.booking_id)}` : ''}
      </div>
      <div class="muted">${esc(n.detail || '')}</div>
      ${n.problem ? `<div class="notewarn">${esc(n.problem)}</div>` : ''}
      <div class="faint">
        written ${esc((n.created_at || '').slice(0, 10))}
        &middot; ${n.applies_to === 'next' ? 'next payroll' : 'week of ' + esc(n.applies_to)}
        ${n.status !== 'open' ? ` &middot; went into the payroll on ${esc((n.applied_at || '').slice(0, 10))}` : ''}
        ${!n.applies_itself && n.status === 'open' ? ' &middot; you will need to do this one yourself' : ''}
      </div>
    </div>
    <div class="noteactions">
      ${n.status === 'open'
        ? `<button class="btn btn-sm" onclick="markNoteDone('${n.id}')">Mark done</button>`
        : `<button class="btn btn-sm" onclick="reopenNote('${n.id}')">Reopen</button>`}
      <button class="btn btn-sm btn-ghost" onclick="deleteNote('${n.id}')">Delete</button>
    </div>
  </div>`;

function noteKindChanged() {
  const kind = $('#nkind').value;
  $('#nhelp').textContent = NOTE_HELP[kind] || '';
  $('#nbookingwrap').hidden = !(kind === 'hours' || kind === 'rate');
  $('#namountwrap').hidden = (kind === 'exclude' || kind === 'check' || kind === 'other');
  $('#ndatewrap').hidden = $('#napplies').value !== 'pick';
  const amount = $('#namount');
  if (kind === 'hours') amount.placeholder = 'Hours actually worked, e.g. 6';
  else if (kind === 'rate') amount.placeholder = 'Correct hourly rate, e.g. 28';
  else amount.placeholder = '50.00';
}

async function addNote() {
  const kind = $('#nkind').value;
  const applies = $('#napplies').value === 'pick' ? ($('#ndate').value || 'next') : 'next';
  const body = {
    kind,
    caregiver_name: $('#nname').value.trim(),
    amount: $('#namountwrap').hidden ? '' : $('#namount').value.trim(),
    booking_id: $('#nbookingwrap').hidden ? '' : $('#nbooking').value.trim(),
    detail: $('#ndetail').value.trim(),
    applies_to: applies,
  };
  if (!body.detail && !body.caregiver_name) { toast('Say who it is about, or what it is.', true); return; }
  try {
    await post('/api/notes', body);
    $('#nname').value = ''; $('#namount').value = '';
    $('#nbooking').value = ''; $('#ndetail').value = '';
    S.notes = await api('/api/notes');
    await render();
    toast('Note saved.');
  } catch (e) { toast(e.message, true); }
}

async function markNoteDone(id) {
  await post('/api/notes/' + id, { status: 'done' });
  S.notes = await api('/api/notes'); await render();
}

async function reopenNote(id) {
  await post('/api/notes/' + id, { status: 'open' });
  S.notes = await api('/api/notes'); await render();
}

async function deleteNote(id) {
  if (!confirm('Delete this note? The payroll it was for will not know about it.')) return;
  await del('/api/notes/' + id);
  S.notes = await api('/api/notes'); await render();
}

VIEWS.after_notes = () => { noteKindChanged(); };

/* Applying this payroll's waiting notes, from the payroll check screen. */
async function applyNotes() {
  try {
    const res = await post(`/api/runs/${S.runId}/notes/apply`);
    await refreshRun();
    if (res.skipped.length) {
      toast(`${res.applied} added. ${res.skipped.length} still need you.`);
    } else {
      toast(`${plural(res.applied, 'note', 'notes')} added to this payroll.`);
    }
  } catch (e) { toast(e.message, true); }
}

/* =====================================================================
   RECURRING PAY — people the bookings export never sees
   ===================================================================== */
function recurringSection() {
  const entries = (S.recurring && S.recurring.entries) || [];
  return `
  <div class="card">
    <h3>Recurring and non-booking pay</h3>
    <p class="muted" style="margin:4px 0 14px">People paid for work that never shows up as a
      booking — a monthly salary, admin hours, phone days, training. Each one gets its own line
      on the payrolls it is due, with no hours and no overtime behind it.</p>

    ${entries.length ? `<div class="tablewrap">
      <table><thead><tr><th>Who</th><th>Amount</th><th>How often</th><th>Taxable</th>
        <th>Active</th><th>Note</th><th></th></tr></thead><tbody>
      ${entries.map(e => `<tr>
        <td><strong>${esc(e.person_name)}</strong></td>
        <td><input type="text" value="${esc(e.amount)}" data-r="amount" style="width:90px"
             onchange="saveRecurring('${e.id}', this)"></td>
        <td><select data-r="frequency" onchange="saveRecurring('${e.id}', this)">
          <option value="monthly" ${e.frequency === 'monthly' ? 'selected' : ''}>Monthly — first Monday</option>
          <option value="weekly" ${e.frequency === 'weekly' ? 'selected' : ''}>Every payroll</option>
          <option value="one_off" ${e.frequency === 'one_off' ? 'selected' : ''}>Paused</option>
        </select></td>
        <td><input type="checkbox" data-r="taxable" ${Number(e.taxable) ? 'checked' : ''}
             onchange="saveRecurring('${e.id}', this)"></td>
        <td><input type="checkbox" data-r="active" ${Number(e.active) ? 'checked' : ''}
             onchange="saveRecurring('${e.id}', this)"></td>
        <td><input type="text" value="${esc(e.note || '')}" data-r="note" style="min-width:150px"
             onchange="saveRecurring('${e.id}', this)"></td>
        <td><button class="btn btn-sm btn-ghost" onclick="deleteRecurring('${e.id}')">Remove</button></td>
      </tr>`).join('')}
      </tbody></table>
    </div>` : '<p class="muted">Nobody set up yet.</p>'}

    <div class="noteform" style="margin-top:16px">
      <label>Who <input type="text" id="rname" placeholder="Name"></label>
      <label>Amount <input type="text" id="ramount" placeholder="0.00" inputmode="decimal"></label>
      <label>How often
        <select id="rfreq">
          <option value="monthly">Monthly — first Monday</option>
          <option value="weekly">Every payroll</option>
        </select>
      </label>
      <label>What for <input type="text" id="rnote" placeholder="What it is for"></label>
    </div>
    <div style="margin-top:12px"><button class="btn" onclick="addRecurring()">Add</button></div>
  </div>`;
}

async function addRecurring() {
  const body = {
    person_name: $('#rname').value.trim(),
    amount: $('#ramount').value.trim(),
    frequency: $('#rfreq').value,
    schedule: 'first_monday',
    taxable: true,
    note: $('#rnote').value.trim(),
  };
  try {
    await post('/api/recurring', body);
    $('#rname').value = ''; $('#ramount').value = ''; $('#rnote').value = '';
    S.recurring = await api('/api/recurring'); await render();
    toast('Added.');
  } catch (e) { toast(e.message, true); }
}

async function saveRecurring(id, input) {
  const row = input.closest('tr');
  const get = (f) => { const el = row.querySelector(`[data-r="${f}"]`); return el ? el.value : ''; };
  const on = (f) => { const el = row.querySelector(`[data-r="${f}"]`); return el ? el.checked : false; };
  try {
    await post('/api/recurring/' + id, {
      amount: get('amount'), frequency: get('frequency'), note: get('note'),
      taxable: on('taxable'), active: on('active'),
    });
    S.recurring = await api('/api/recurring');
    toast('Saved.');
  } catch (e) { toast(e.message, true); }
}

async function deleteRecurring(id) {
  if (!confirm('Remove this person from recurring pay?')) return;
  await del('/api/recurring/' + id);
  S.recurring = await api('/api/recurring'); await render();
}
