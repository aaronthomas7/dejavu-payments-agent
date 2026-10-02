/* DejaVu front end - plain JS, no build step. */
"use strict";

// Recorded-demo mode: the same UI served as static files (e.g. GitHub Pages). Every answer
// comes from JSON captured during a real run by scripts/export_demo.py; nothing calls an API.
const STATIC = !!window.DEJAVU_STATIC;

const S = {
  status: null, taxonomy: [], tax: {}, entities: null,
  filter: "open", cases: [], selectedId: null, caseData: null,
  diag: null, compare: null, busy: false,
  replay: null, chart: null, chartMode: "rolling", playing: false,
  loaded: { curve: false, precheck: false, network: false },
  inputs: { suggestions: [], presets: [] }, presetIndex: null, presetDirty: false,
};

// ---------------------------------------------------------------- helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = (text) => DOMPurify.sanitize(marked.parse(String(text || "")));
const pct = (v) => (v === null || v === undefined ? "–" : `${Math.round(v * 100)}%`);

async function api(path, opts = {}) {
  if (STATIC) return staticApi(path, opts);
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    const detail = data && data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText;
    throw new Error(detail);
  }
  return data;
}

// ---------------------------------------------------------------- recorded demo (static files)
const DEMO = { cache: {}, open: null, resolved: null, decisions: {} };

async function demoFile(name) {
  if (!(name in DEMO.cache)) {
    const res = await fetch(`data/${name}`);
    if (!res.ok) throw new Error("Not part of the recorded demo.");
    DEMO.cache[name] = await res.json();
  }
  return JSON.parse(JSON.stringify(DEMO.cache[name]));
}

async function demoCases() {
  if (!DEMO.open) {
    DEMO.open = await demoFile("cases_open.json");
    DEMO.resolved = await demoFile("cases_resolved.json");
  }
}

async function staticApi(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const url = new URL(path, "http://demo.local");
  const p = url.pathname;
  const body = opts.body || {};
  if (p === "/api/status") {
    await demoCases();
    const st = await demoFile("status.json");
    st.cases = { open: DEMO.open.length, resolved: DEMO.resolved.length, pending_retains: 0 };
    return st;
  }
  if (p === "/api/taxonomy") return demoFile("taxonomy.json");
  if (p === "/api/entities") return demoFile("entities.json");
  if (p === "/api/replay") return demoFile("replay.json");
  if (p === "/api/memory/lessons") return demoFile("lessons.json");
  if (p === "/api/memory/playbook") return demoFile("playbook.json");
  if (p === "/api/memory/playbook/refresh") throw new Error("In the recorded demo the playbook is shown as it was captured.");
  if (p === "/api/cases") {
    await demoCases();
    return JSON.parse(JSON.stringify(url.searchParams.get("status") === "resolved" ? DEMO.resolved : DEMO.open));
  }
  const m = p.match(/^\/api\/cases\/([^/]+)(\/(diagnose|resolve))?$/);
  if (m) {
    await demoCases();
    const id = decodeURIComponent(m[1]);
    const c = DEMO.open.find((x) => x.case_id === id) || DEMO.resolved.find((x) => x.case_id === id);
    if (!c) throw new Error(`Case ${id} not found`);
    if (!m[3]) return JSON.parse(JSON.stringify(c));
    if (m[3] === "diagnose") {
      try { return await demoFile(`diagnose/${id}.${body.use_memory ? "on" : "off"}.json`); }
      catch (_) { throw new Error("In the recorded demo, diagnoses were captured for the open cases only."); }
    }
    // resolve: keep the decision in this browser tab only
    const res = { root_cause: body.root_cause, note: body.note, agent_root_cause: body.agent_root_cause, recorded_demo: true };
    DEMO.open = DEMO.open.filter((x) => x.case_id !== id);
    DEMO.resolved.unshift({ ...c, status: "resolved", resolution: res });
    return {
      case_id: id, root_cause: body.root_cause, retained: false, queued_for_retry: false,
      agent_was_right: body.agent_root_cause ? body.agent_root_cause === body.root_cause : null,
      message: "Recorded demo, so this decision isn't saved. Run DejaVu locally to teach it for real.",
    };
  }
  if (p === "/api/memory/ask") {
    const i = S.inputs.suggestions.indexOf((body.question || "").trim());
    if (i < 0) throw new Error("This is a recorded demo: pick one of the suggested questions above.");
    return demoFile(`ask/${i}.json`);
  }
  if (p === "/api/precheck") {
    if (S.presetIndex === null || S.presetDirty) throw new Error("This is a recorded demo: choose one of the example payments above, then run the check.");
    return demoFile(`precheck/${S.presetIndex}.json`);
  }
  throw new Error("Not available in the recorded demo.");
}

let toastTimer = null;
function toast(msg, kind = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 4200);
}

const SGT = "Asia/Singapore";
function fmtDate(iso, withYear = false) {
  if (!iso) return "–";
  const d = new Date(iso);
  const opts = { timeZone: SGT, weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false };
  if (withYear) opts.year = "numeric";
  return new Intl.DateTimeFormat("en-GB", opts).format(d) + " SGT";
}
function fmtDay(iso) {
  if (!iso) return "";
  return new Intl.DateTimeFormat("en-GB", { timeZone: SGT, day: "2-digit", month: "short" }).format(new Date(iso));
}
function fmtMoney(amount, ccy) {
  const digits = ["IDR", "INR", "PHP"].includes(ccy) ? 0 : 2;
  return `${ccy} ${new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(amount)}`;
}
const EXC_LABEL = {
  REJECTED: "Rejected", RETURNED: "Returned", HELD: "Held", SCREENING_HOLD: "Sanctions screening hold",
  NON_RECEIPT_CLAIM: "Non-receipt claim",
};
function codeBadge(c) {
  if (c.exception_type === "SCREENING_HOLD") return `<span class="badge red">SCREENING</span>`;
  if (c.exception_type === "NON_RECEIPT_CLAIM") return `<span class="badge blue">NON-RECEIPT</span>`;
  return `<span class="badge code">${esc(c.reason_code || c.exception_type)}</span>`;
}
function rcLabel(code) { return (S.tax[code] && S.tax[code].label) || code || "–"; }
function isTyping(e) { return ["INPUT", "TEXTAREA", "SELECT"].includes((e.target || {}).tagName); }

// ---------------------------------------------------------------- status
async function loadStatus() {
  try {
    S.status = await api("/api/status");
  } catch (e) {
    $("#status").innerHTML = `<span class="pill bad">Server unreachable</span>`;
    return;
  }
  const st = S.status;
  const counts = st.memory_counts || {};
  const facts = (counts.world || 0) + (counts.experience || 0);
  const memPill = st.setup_error ? `<span class="pill bad" title="${esc(st.setup_error)}">Memory setup failed</span>`
    : st.memory_backend === "hindsight" ? `<span class="pill ok">Hindsight · ${esc(st.bank_id)}</span>`
    : `<span class="pill warn">Memory: offline stub</span>`;
  const llmPill = st.llm_backend === "groq" ? `<span class="pill ok">Groq · ${esc(st.model)}</span>` : `<span class="pill warn">Model: offline heuristic</span>`;
  const memCount = counts.error ? "" : `<span class="pill">${facts} memories · ${counts.observation ?? 0} lessons</span>`;
  const demoPill = STATIC ? `<span class="pill warn">Recorded demo</span>` : "";
  $("#status").innerHTML = demoPill + memPill + llmPill + memCount;
  $("#count-open").textContent = st.cases ? `(${st.cases.open})` : "";
  $("#count-resolved").textContent = st.cases ? `(${st.cases.resolved})` : "";

  const banner = $("#offline-banner");
  if (STATIC) {
    const when = st.recorded_at ? ` on ${esc(fmtDate(st.recorded_at, true))}` : "";
    const repo = st.repo_url ? ` <a href="${esc(st.repo_url)}" target="_blank" rel="noopener">Run it live from the repo</a>.` : "";
    banner.innerHTML = st.offline
      ? `<b>Offline test capture, not real results.</b> Re-run scripts/export_demo.py with API keys before publishing.`
      : `<b>Recorded demo.</b> Every answer on this page was captured from a real run of DejaVu (Hindsight memory + Groq)${when}. Decisions you make here are not saved.${repo}`;
    banner.classList.remove("hidden");
    return;
  }
  if (st.setup_error) {
    banner.innerHTML = `Memory setup failed: <b>${esc(st.setup_error)}</b>. Check HINDSIGHT_API_KEY in .env, then restart. Diagnoses still work without memory.`;
    banner.classList.remove("hidden");
  } else if (st.offline) {
    banner.innerHTML = `<b>Offline development mode.</b> Using local stand-ins instead of ${st.memory_backend !== "hindsight" ? "Hindsight" : ""}${st.memory_backend !== "hindsight" && st.llm_backend !== "groq" ? " and " : ""}${st.llm_backend !== "groq" ? "Groq" : ""}. Add your API keys to <code>.env</code> for the real agent.`;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

// ---------------------------------------------------------------- tabs
function showTab(name) {
  $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "curve") loadCurve();
  if (name === "learned") { loadLessons(); loadPlaybook(); }
  if (name === "precheck" && !S.loaded.precheck) initPrecheck();
  if (name === "network" && !S.loaded.network) initNetwork();
  if (name === "desk" && SIM.newCount) {
    clearDeskNew();
    loadStatus();
    loadCases(S.selectedId);
  }
}

// ---------------------------------------------------------------- queue
async function loadCases(selectId) {
  S.cases = await api(`/api/cases?status=${S.filter}`);
  S.cases.sort((a, b) => (S.filter === "open" ? a.created_at.localeCompare(b.created_at) : b.created_at.localeCompare(a.created_at)));
  renderQueue();
  const target = selectId && S.cases.find((c) => c.case_id === selectId) ? selectId : (S.cases[0] && S.cases[0].case_id);
  if (target) selectCase(target);
  else $("#case-panel").innerHTML = `<div class="empty">${S.filter === "open" ? "Queue is empty. Nice work." : "Nothing resolved yet."}</div>`;
}

function renderQueue() {
  $("#queue-list").innerHTML = S.cases.map((c) => {
    const p = c.payment;
    const r = c.resolution;
    let tail = `<span class="muted small">${esc(fmtDay(c.created_at))}</span>`;
    if (r) {
      const right = r.agent_root_cause ? (r.agent_root_cause === r.root_cause ? `<span class="ok">✓</span>` : `<span class="no">✗</span>`) : "";
      tail = `<span class="badge teal">${esc(rcLabel(r.root_cause))}</span> ${right}`;
    }
    return `<div class="qitem ${c.case_id === S.selectedId ? "active" : ""}" data-id="${esc(c.case_id)}" tabindex="0">
      <div class="qrow"><span class="qbank">${esc(p.creditor_bank.name)}</span>${codeBadge(c)}</div>
      <div class="qsub">${esc(p.debtor.name)} → ${esc(p.creditor.name)}</div>
      <div class="qrow"><span class="amount">${esc(fmtMoney(p.amount, p.currency))}</span>${tail}</div>
    </div>`;
  }).join("") || `<div class="empty small">No cases.</div>`;
}

async function selectCase(id) {
  S.selectedId = id;
  S.diag = null;
  S.compare = null;
  $$(".qitem").forEach((el) => el.classList.toggle("active", el.dataset.id === id));
  S.caseData = await api(`/api/cases/${encodeURIComponent(id)}`);
  renderCase();
}

function renderCase() {
  const c = S.caseData;
  if (!c) return;
  const p = c.payment;
  const statusBadge = c.status === "open" ? `<span class="badge amber">OPEN</span>` : `<span class="badge teal">RESOLVED</span>`;
  const r = c.resolution;
  const resolved = r ? `<div class="resolved-box"><b>Resolved:</b> ${esc(rcLabel(r.root_cause))}
      ${r.agent_root_cause ? (r.agent_root_cause === r.root_cause ? ` · <span class="ok">DejaVu was right</span>` : ` · <span class="no">DejaVu said ${esc(rcLabel(r.agent_root_cause))}</span>`) : ""}
      <div class="small" style="margin-top:4px">${esc(r.note || "")}</div></div>` : "";
  $("#case-panel").innerHTML = `
    <div class="case-title">
      <h1>${esc(p.creditor_bank.name)}</h1>${codeBadge(c)}
      <span class="badge">${esc(EXC_LABEL[c.exception_type] || c.exception_type)}${c.reason_text ? " · " + esc(c.reason_text) : ""}</span>
      ${statusBadge}
      <span class="muted">${esc(c.case_id)} · opened ${esc(fmtDate(c.created_at))}</span>
    </div>
    <div class="card">
      <h3>What happened</h3>
      <div class="what">${esc(c.counterparty_message)}</div>
      ${c.tracker ? `<div class="tracker">${esc(c.tracker)}</div>` : ""}
      ${c.debtor_balance_check ? `<div class="tracker">Client balance check: <b>${esc(c.debtor_balance_check)}</b></div>` : ""}
      <div class="kv">
        <div><div class="k">Amount</div><div class="v amount">${esc(fmtMoney(p.amount, p.currency))}</div></div>
        <div><div class="k">Client</div><div class="v">${esc(p.debtor.name)}</div></div>
        <div><div class="k">Beneficiary</div><div class="v">${esc(p.creditor.name)}</div></div>
        <div><div class="k">Beneficiary account</div><div class="v mono">${esc(p.creditor.account)}</div></div>
        <div><div class="k">Beneficiary bank</div><div class="v">${esc(p.creditor_bank.name)} <span class="mono muted">${esc(p.creditor_bank.bic)}</span></div></div>
        <div><div class="k">Intermediary</div><div class="v">${p.intermediary_bank ? esc(p.intermediary_bank.name) + ` <span class="mono muted">${esc(p.intermediary_bank.bic)}</span>` : "–"}</div></div>
        <div><div class="k">Submitted</div><div class="v">${esc(fmtDate(p.submitted_at))}</div></div>
        <div><div class="k">Value date</div><div class="v">${esc(p.value_date)}</div></div>
        <div><div class="k">Remittance info</div><div class="v">${esc(p.remittance_info || "–")}</div></div>
      </div>
      ${resolved}
    </div>
    <div class="actions">
      <label class="switch" title="Toggle Hindsight memory for this diagnosis">
        <input type="checkbox" id="mem-toggle" checked><span class="track"></span> Use memory
      </label>
      <button class="btn primary big" id="btn-diagnose">Diagnose <span class="kbd">D</span></button>
      <button class="btn" id="btn-compare">Compare with / without memory <span class="kbd">C</span></button>
    </div>
    <div id="diag-area"></div>`;
  $("#btn-diagnose").onclick = () => diagnose();
  $("#btn-compare").onclick = () => compare();
}

// ---------------------------------------------------------------- diagnosis
function thinking(text) {
  return `<div class="card thinking"><span class="spinner"></span>${esc(text)}</div>`;
}

async function diagnose() {
  if (S.busy || !S.caseData) return;
  const useMemory = $("#mem-toggle").checked;
  S.busy = true;
  S.compare = null;
  setBusyButtons(true);
  $("#diag-area").innerHTML = thinking(useMemory ? "Recalling similar past exceptions from Hindsight, then reasoning…" : "Reasoning without memory…");
  try {
    S.diag = await api(`/api/cases/${encodeURIComponent(S.caseData.case_id)}/diagnose`, { method: "POST", body: { use_memory: useMemory } });
    $("#diag-area").innerHTML = renderDiag(S.diag) + renderResolveBar(S.diag);
    wireDiag();
  } catch (e) {
    $("#diag-area").innerHTML = `<div class="card note warn">Diagnosis failed: ${esc(e.message)}</div>`;
  } finally {
    S.busy = false;
    setBusyButtons(false);
  }
}

async function compare() {
  if (S.busy || !S.caseData) return;
  S.busy = true;
  setBusyButtons(true);
  $("#diag-area").innerHTML = thinking("Asking the same model twice: once with no memory, once with Hindsight memory…");
  const id = encodeURIComponent(S.caseData.case_id);
  try {
    const [off, on] = await Promise.all([
      api(`/api/cases/${id}/diagnose`, { method: "POST", body: { use_memory: false } }),
      api(`/api/cases/${id}/diagnose`, { method: "POST", body: { use_memory: true } }),
    ]);
    S.compare = { off, on };
    S.diag = on;
    $("#diag-area").innerHTML = `<div class="compare">
        <div><div class="col-title off">Without memory</div>${renderDiag(off, true)}</div>
        <div><div class="col-title on">With Hindsight memory</div>${renderDiag(on, true)}</div>
      </div>` + renderResolveBar(on);
    wireDiag();
  } catch (e) {
    $("#diag-area").innerHTML = `<div class="card note warn">Comparison failed: ${esc(e.message)}</div>`;
  } finally {
    S.busy = false;
    setBusyButtons(false);
  }
}

function setBusyButtons(busy) {
  ["#btn-diagnose", "#btn-compare"].forEach((sel) => { const b = $(sel); if (b) b.disabled = busy; });
}

function renderDiag(d, compact = false) {
  const conf = Math.round((d.confidence || 0) * 100);
  const confClass = conf >= 80 ? "" : conf >= 55 ? "mid" : "low";
  const badges = [];
  if (d.used_memory) badges.push(d.evidence.length ? `<span class="badge teal">Seen before · ${d.evidence.length} past case${d.evidence.length > 1 ? "s" : ""}</span>` : `<span class="badge">No matching history</span>`);
  if (d.requires_human_approval) badges.push(`<span class="badge red">Human approval required</span>`);
  if (d.auto_fix_eligible) badges.push(`<span class="badge teal">One-click fix</span>`);
  if (d.degraded) badges.push(`<span class="badge amber">Degraded</span>`);

  const evidence = d.evidence.length ? `<div class="section"><h3>Evidence from memory</h3><div class="evidence">${d.evidence.map((e) => `
      <div class="ev">
        <div class="ev-top"><span class="badge">${esc(e.memory_ref)}</span>${e.case_id ? `<span class="mono">${esc(e.case_id)}</span>` : ""}
          ${e.occurred ? `<span class="muted small">${esc(fmtDay(e.occurred))}</span>` : ""}${e.type ? `<span class="badge blue">${esc(e.type)}</span>` : ""}</div>
        <div class="ev-why">${esc(e.why_relevant)}</div>
        ${e.text ? `<details><summary>Show memory</summary><pre>${esc(e.text)}</pre></details>` : ""}
      </div>`).join("")}</div></div>` : "";

  const notes = [
    ...(d.guardrail_notes || []).map((n) => `<div class="note guard">🛡 ${esc(n)}</div>`),
    ...(d.warnings || []).map((n) => `<div class="note warn">${esc(n)}</div>`),
  ].join("");

  const recalled = d.recalled && d.recalled.length ? `<details><summary>${d.recalled.length} memories recalled</summary>${d.recalled.map((m) => `
      <div class="ev" style="margin-top:6px"><div class="ev-top"><span class="badge">${esc(m.ref)}</span><span class="badge blue">${esc(m.type || "memory")}</span>
      ${m.occurred ? `<span class="muted small">${esc(fmtDay(m.occurred))}</span>` : ""}${m.case_id ? `<span class="mono small">${esc(m.case_id)}</span>` : ""}</div>
      <pre class="memtext">${esc(m.text)}</pre></div>`).join("")}</details>` : "";

  return `<div class="card diag ${d.used_memory ? "" : "off"}">
    <div class="diag-head">
      <div>
        <div class="diag-rc">${esc(d.root_cause_label)}</div>
        <div class="diag-code">${esc(d.root_cause)}</div>
        <div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap">${badges.join("")}</div>
      </div>
      <div class="conf"><div class="small muted">Confidence <b style="color:var(--ink)">${conf}%</b></div>
        <div class="conf-bar"><div class="conf-fill ${confClass}" style="width:${conf}%"></div></div></div>
    </div>
    <div class="section"><h3>Why</h3><p>${esc(d.reasoning)}</p></div>
    <div class="section"><h3>Recommended action</h3><p>${esc(d.recommended_action)}</p></div>
    ${evidence}
    ${compact ? "" : (d.draft_message ? `<div class="section"><h3>Draft message</h3><div class="draft">${esc(d.draft_message)}<button class="btn copy" data-copy="${esc(d.draft_message)}">Copy</button></div></div>` : "")}
    ${d.prevention_tip ? `<div class="section"><h3>Prevent it next time</h3><p>${esc(d.prevention_tip)}</p></div>` : ""}
    ${notes ? `<div class="notes">${notes}</div>` : ""}
    <div class="meta"><span>${esc(d.model)}</span><span>${d.latency_ms} ms</span>${d.used_memory ? `<span>${d.recalled.length} memories recalled</span>` : "<span>memory off</span>"}</div>
    ${compact ? "" : recalled}
  </div>`;
}

function renderResolveBar(d) {
  if (!S.caseData || S.caseData.status !== "open") return "";
  const hint = S.caseData.demo_hint || {};
  const options = S.taxonomy.map((t) => `<option value="${esc(t.code)}" ${t.code === (hint.root_cause || d.root_cause) ? "selected" : ""}>${esc(t.label)} (${esc(t.code)})</option>`).join("");
  const approveLabel = d.requires_human_approval ? "Route to Compliance & record" : "Approve & teach DejaVu";
  return `<div class="card">
    <div class="resolve-bar" style="margin-top:0;padding-top:0;border-top:0">
      <button class="btn primary" id="btn-approve">${approveLabel} <span class="kbd">A</span></button>
      <button class="btn" id="btn-correct">Correct it</button>
      <span class="muted small">${STATIC ? "Recorded demo: your decision stays in this browser tab." : "Your decision is written to Hindsight memory, so DejaVu learns from it."}</span>
    </div>
    <form class="resolve-form hidden" id="correct-form">
      <label>Actual root cause<select id="correct-rc">${options}</select></label>
      <label>Investigation notes<textarea id="correct-note" placeholder="What did you find and how did you fix it?">${esc(hint.note || "")}</textarea></label>
      <div style="display:flex;gap:8px"><button class="btn primary" type="submit">Save correction to memory</button>
        <button class="btn ghost" type="button" id="cancel-correct">Cancel</button></div>
    </form></div>`;
}

function wireDiag() {
  $$(".copy").forEach((b) => (b.onclick = () => {
    navigator.clipboard.writeText(b.dataset.copy || "").then(() => toast("Draft copied"), () => toast("Copy failed", "bad"));
  }));
  const approve = $("#btn-approve");
  if (approve) approve.onclick = () => approveDiag();
  const correct = $("#btn-correct");
  if (correct) correct.onclick = () => { $("#correct-form").classList.remove("hidden"); $("#correct-note").focus(); };
  const cancel = $("#cancel-correct");
  if (cancel) cancel.onclick = () => $("#correct-form").classList.add("hidden");
  const form = $("#correct-form");
  if (form) form.onsubmit = (e) => {
    e.preventDefault();
    const note = $("#correct-note").value.trim();
    if (note.length < 3) { toast("Add a short investigation note so DejaVu can learn from it", "bad"); return; }
    resolveCase($("#correct-rc").value, note);
  };
}

async function approveDiag() {
  const d = S.diag;
  if (!d || S.busy) return;
  const hint = S.caseData.demo_hint || {};
  const note = hint.note && hint.root_cause === d.root_cause ? hint.note : `${d.recommended_action} ${d.reasoning}`.trim();
  await resolveCase(d.root_cause, note);
}

async function resolveCase(rootCause, note) {
  const d = S.diag;
  S.busy = true;
  try {
    const res = await api(`/api/cases/${encodeURIComponent(S.caseData.case_id)}/resolve`, {
      method: "POST",
      body: { root_cause: rootCause, note, agent_root_cause: d ? d.root_cause : null, agent_confidence: d ? d.confidence : null },
    });
    const verdict = res.agent_was_right === true ? "DejaVu was right. " : res.agent_was_right === false ? "Correction saved. " : "";
    toast(verdict + res.message, res.retained ? "ok" : "");
    const idx = S.cases.findIndex((c) => c.case_id === S.caseData.case_id);
    const next = S.cases[idx + 1] || S.cases[idx - 1];
    await loadStatus();
    await loadCases(next ? next.case_id : null);
  } catch (e) {
    toast(`Could not resolve: ${e.message}`, "bad");
  } finally {
    S.busy = false;
  }
}

// ---------------------------------------------------------------- learning curve
async function loadCurve() {
  const box = $("#curve-panel");
  try {
    S.replay = await api("/api/replay");
  } catch (e) {
    box.innerHTML = `<div class="card note warn">Could not load replay results: ${esc(e.message)}</div>`;
    return;
  }
  const r = S.replay;
  if (!r.available) {
    box.innerHTML = `<div class="card"><h2>No replay yet</h2>
      <p class="muted">Run the 6-week replay once to measure how DejaVu learns. It starts from an empty memory bank,
      diagnoses every past exception with and without memory, then teaches Hindsight the real resolution.</p>
      <pre class="draft">python scripts/replay.py --fresh</pre></div>`;
    return;
  }
  const s = r.summary;
  // KPI percentages from the per-case rows: the stored summary is rounded to 3 decimals, and rounding twice
  // can turn 38.46% into 39%.
  const frac = (rows, key) => (rows.length ? rows.filter((c) => c[key]).length / rows.length : null);
  const reps = r.cases.filter((c) => c.is_repeat);
  const half = Math.floor(r.cases.length / 2);
  const k = {
    accOn: frac(r.cases, "on_correct"), accOff: frac(r.cases, "off_correct"),
    repOn: frac(reps, "on_correct"), repOff: frac(reps, "off_correct"),
    h1: frac(r.cases.slice(0, half), "on_correct"), h2: frac(r.cases.slice(half), "on_correct"),
  };
  box.innerHTML = `
    ${r.offline ? `<div class="card note warn" style="margin-bottom:14px">These results came from the offline stand-ins, not Hindsight + Groq. Re-run the replay with API keys for real numbers.</div>` : ""}
    <div class="kpis">
      <div class="card kpi"><div class="label">Root-cause accuracy</div><div class="value">${pct(k.accOn)} <span class="off">vs ${pct(k.accOff)}</span></div><div class="vs">with memory vs same model without</div></div>
      <div class="card kpi"><div class="label">On patterns seen before</div><div class="value">${pct(k.repOn)} <span class="off">vs ${pct(k.repOff)}</span></div><div class="vs">${s.repeat_cases} repeat cases</div></div>
      <div class="card kpi"><div class="label">Gets better over time</div><div class="value">${pct(k.h1)} → ${pct(k.h2)}</div><div class="vs">first half → second half (memory ON)</div></div>
      <div class="card kpi"><div class="label">Analyst time (estimate)</div><div class="value">${Math.round(s.minutes_manual / 60)}h → ${Math.round(s.minutes_with_dejavu_estimate / 60)}h</div><div class="vs">if confident, correct answers take 5 min to verify</div></div>
    </div>
    <div class="card">
      <div class="card-head">
        <h2>Learning curve <span class="muted small">${s.cases} exceptions replayed in date order · ${esc(r.mode.model)} · bank ${esc(r.bank_id)}</span></h2>
        <div style="display:flex;gap:8px;align-items:center">
          <div class="seg"><button class="seg-btn ${S.chartMode === "rolling" ? "active" : ""}" data-mode="rolling">Rolling ${s.rolling_window}</button>
          <button class="seg-btn ${S.chartMode === "cumulative" ? "active" : ""}" data-mode="cumulative">Cumulative</button></div>
          <button class="btn primary" id="btn-play">▶ Replay the weeks</button>
        </div>
      </div>
      <div class="chart-box"><canvas id="curve-chart"></canvas></div>
      <div class="ticker" id="ticker"></div>
    </div>
    <div class="card"><div class="card-head"><h2>Every case</h2></div>
      <div class="table-wrap"><table><thead><tr><th>#</th><th>Date</th><th>Bank</th><th>Error</th><th>Actual root cause</th><th>With memory</th><th>Without memory</th><th>Evidence used</th></tr></thead>
      <tbody>${r.cases.map((c, i) => `<tr>
        <td>${i + 1}</td><td>${esc(c.date.slice(5))}</td><td>${esc(c.bank)}</td><td class="mono">${esc(c.code)}</td>
        <td>${esc(rcLabel(c.truth))}${c.is_repeat ? ` <span class="badge">repeat</span>` : ""}</td>
        <td class="${c.on_correct ? "ok" : "no"}">${c.on_correct ? "✓" : "✗"} ${c.on_correct ? "" : esc(rcLabel(c.on_root_cause))} <span class="muted small">${pct(c.on_confidence)}</span></td>
        <td class="${c.off_correct ? "ok" : "no"}">${c.off_correct ? "✓" : "✗"} ${c.off_correct ? "" : esc(rcLabel(c.off_root_cause))}</td>
        <td class="mono small">${esc((c.on_evidence || []).join(", "))}</td></tr>`).join("")}</tbody></table></div></div>`;
  $$("[data-mode]", box).forEach((b) => (b.onclick = () => { S.chartMode = b.dataset.mode; loadCurve(); }));
  $("#btn-play").onclick = () => playReplay();
  $("#ticker").innerHTML = `<span class="muted small">Points on the teal line: <span class="ok">teal</span> = DejaVu right on that case,
    <span class="no">red</span> = wrong. Hover a point for the case details, or press ▶ to watch the weeks replay.</span>`;
  drawChart(r.cases.length);
}

function seriesFor(mode) {
  const s = S.replay.summary;
  return mode === "cumulative" ? [s.cumulative_on, s.cumulative_off] : [s.rolling_on, s.rolling_off];
}

function drawChart(upto) {
  const rows = S.replay.cases;
  const [on, off] = seriesFor(S.chartMode);
  const n = rows.length;
  const labels = rows.map((_, i) => i + 1);
  const cut = (arr) => arr.map((v, i) => (i < upto ? Math.round(v * 100) : null));
  const pointColors = rows.map((c) => (c.on_correct ? "#0f766e" : "#dc2626"));
  const ctx = $("#curve-chart");
  if (S.chart) S.chart.destroy();
  S.chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "DejaVu with Hindsight memory", data: cut(on), borderColor: "#0f766e", backgroundColor: "rgba(15,118,110,.08)",
          fill: true, tension: .25, borderWidth: 3, pointRadius: 3.5, pointBackgroundColor: pointColors, pointBorderWidth: 0 },
        { label: "Same model, no memory", data: cut(off), borderColor: "#94a3b8", borderDash: [6, 5], tension: .25,
          borderWidth: 2, pointRadius: 0 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
      scales: {
        y: { min: 0, max: 100, ticks: { callback: (v) => v + "%" }, title: { display: true, text: "Root-cause accuracy" } },
        x: { title: { display: true, text: `Exception # (chronological, ${rows[0].date} → ${rows[n - 1].date})` }, ticks: { maxTicksLimit: 12 } },
      },
      plugins: {
        legend: { position: "bottom" },
        tooltip: { callbacks: { afterBody: (items) => {
          const c = rows[items[0].dataIndex];
          return [`${c.case_id} · ${c.bank} · ${c.code}`, `Actual: ${rcLabel(c.truth)}`,
            `With memory: ${c.on_correct ? "✓" : "✗ " + rcLabel(c.on_root_cause)}`, `Without: ${c.off_correct ? "✓" : "✗ " + rcLabel(c.off_root_cause)}`];
        } } },
      },
    },
  });
}

function playReplay() {
  if (S.playing) return;
  S.playing = true;
  const rows = S.replay.cases;
  let i = 0;
  const btn = $("#btn-play");
  btn.disabled = true;
  const tick = () => {
    i += 1;
    drawChartIncrement(i);
    const c = rows[i - 1];
    $("#ticker").innerHTML = `<span class="badge">${i}/${rows.length}</span><span class="mono">${esc(c.case_id)}</span>
      <b>${esc(c.bank)}</b><span class="badge code">${esc(c.code)}</span>
      <span>with memory <span class="${c.on_correct ? "ok" : "no"}">${c.on_correct ? "✓" : "✗"}</span></span>
      <span>without <span class="${c.off_correct ? "ok" : "no"}">${c.off_correct ? "✓" : "✗"}</span></span>
      ${c.on_evidence && c.on_evidence.length ? `<span class="muted small">recalled ${esc(c.on_evidence.join(", "))}</span>` : ""}`;
    if (i < rows.length) setTimeout(tick, 220);
    else { S.playing = false; btn.disabled = false; }
  };
  drawChart(0);
  setTimeout(tick, 300);
}

function drawChartIncrement(upto) {
  if (!S.chart) return drawChart(upto);
  const [on, off] = seriesFor(S.chartMode);
  S.chart.data.datasets[0].data = on.map((v, i) => (i < upto ? Math.round(v * 100) : null));
  S.chart.data.datasets[1].data = off.map((v, i) => (i < upto ? Math.round(v * 100) : null));
  S.chart.update("none");
}

// ---------------------------------------------------------------- what it learned
async function loadLessons() {
  const box = $("#lessons");
  box.innerHTML = `<div class="thinking"><span class="spinner"></span>Loading lessons…</div>`;
  try {
    const { items } = await api("/api/memory/lessons?limit=40");
    box.innerHTML = items.length ? items.map((l) => `<div class="lesson">
        <span class="proof" title="independent pieces of evidence">×${esc(l.proof_count || 1)}</span>
        <div><div class="markdown">${md(l.text)}</div><div class="muted small">${l.updated_at ? "updated " + esc(fmtDay(l.updated_at)) : ""}</div></div>
      </div>`).join("")
      : `<div class="empty small">No lessons yet. Hindsight consolidates observations a little after new cases are retained.</div>`;
  } catch (e) {
    box.innerHTML = `<div class="note warn">${esc(e.message)}</div>`;
  }
}

async function loadPlaybook() {
  const box = $("#playbook");
  try {
    const pb = await api("/api/memory/playbook");
    $("#playbook-meta").textContent = pb.last_refreshed_at ? `Last rewritten ${fmtDate(pb.last_refreshed_at, true)}${pb.is_stale ? " · new memories since (refreshing soon)" : ""}` : "";
    box.innerHTML = pb.content ? md(pb.content) : `<div class="empty small">Hindsight is writing the playbook from memory. Check back in a minute.</div>`;
  } catch (e) {
    box.innerHTML = `<div class="note warn">${esc(e.message)}</div>`;
  }
}

async function ask(question) {
  const out = $("#ask-answer");
  out.innerHTML = `<div class="thinking"><span class="spinner"></span>DejaVu is reflecting over the desk's memory…</div>`;
  try {
    const a = await api("/api/memory/ask", { method: "POST", body: { question } });
    const dirs = (a.directives || []).map((d) => `<span class="badge red">🛡 ${esc(d.name || "directive")}</span>`).join("");
    out.innerHTML = `<div class="answer markdown">${md(a.answer)}</div>
      <div class="based-on muted small"><span>Based on ${a.memories.length} memories${a.mental_models.length ? ` · ${a.mental_models.length} mental model(s)` : ""}</span>${dirs}<span>${a.latency_ms} ms</span></div>`;
  } catch (e) {
    out.innerHTML = `<div class="note warn">${esc(e.message)}</div>`;
  }
}

// ---------------------------------------------------------------- pre-flight
async function initPrecheck() {
  S.loaded.precheck = true;
  try {
    S.entities = S.entities || await api("/api/entities");
  } catch (e) {
    toast("Could not load clients/banks", "bad");
    return;
  }
  const opt = (v) => `<option>${esc(v)}</option>`;
  $("#pc-client").innerHTML = Object.values(S.entities.clients).map((c) => opt(c.name)).join("");
  $("#pc-bank").innerHTML = Object.values(S.entities.banks).map((b) => opt(b.name)).join("");
  $("#pc-inter").innerHTML = `<option value="">None</option>` + Object.values(S.entities.intermediaries).map((b) => opt(b.name)).join("");
  const presets = S.inputs.presets;
  $("#precheck-presets").innerHTML = presets.map((p, i) => `<button class="chip" data-preset="${i}">${esc(p.label)}</button>`).join("");
  $$("[data-preset]").forEach((b) => (b.onclick = () => fillPreset(+b.dataset.preset)));
  $("#precheck-form").addEventListener("input", () => { S.presetDirty = true; });
  if (presets.length) fillPreset(0);
}

function fillPreset(i) {
  const v = S.inputs.presets[i].v;
  const f = $("#precheck-form");
  ["client", "beneficiary", "beneficiary_bank", "intermediary", "currency", "amount", "submit_time_sgt", "submit_date",
    "beneficiary_account", "remittance_info"].forEach((k) => {
    if (f.elements[k]) f.elements[k].value = v[k] !== undefined ? v[k] : "";
  });
  S.presetIndex = i;
  S.presetDirty = false;
}

function warningsHtml(r) {
  const dirs = (r.directives || []).map((d) => `<span class="badge red">🛡 ${esc(d.name || "directive")}</span>`).join("");
  return (r.warnings.map((w) => `<div class="warning ${esc(w.severity)}"><b>${esc(w.title)}</b><div>${esc(w.detail)}</div>${w.fix ? `<div class="fix">Fix before sending: ${esc(w.fix)}</div>` : ""}</div>`).join("") || `<div class="muted">No warnings.</div>`)
    + `<div class="based-on muted small"><span>Based on ${r.evidence.length} memories</span>${dirs}<span>${r.latency_ms} ms</span></div>`;
}

async function runPrecheck(e) {
  e.preventDefault();
  const f = e.target;
  const body = Object.fromEntries(new FormData(f).entries());
  body.amount = parseFloat(body.amount);
  ["intermediary", "beneficiary_account", "submit_date"].forEach((k) => { if (!body[k]) delete body[k]; });
  const out = $("#precheck-result");
  out.innerHTML = `<div class="thinking"><span class="spinner"></span>Reflecting over every exception this desk has seen…</div>`;
  try {
    const r = await api("/api/precheck", { method: "POST", body });
    out.innerHTML = `<div class="card-head"><h2>Pre-flight result</h2><span class="muted small">${r.latency_ms} ms</span></div>
      <div class="risk ${esc(r.risk_level)}">${esc(r.risk_level)} risk</div>
      <p>${esc(r.summary)}</p>${warningsHtml(r)}`;
  } catch (err) {
    out.innerHTML = `<div class="note warn">${esc(err.message)}</div>`;
  }
}

// ---------------------------------------------------------------- payment network simulator (live demo)
// Fictional banks with hidden rules stand in for the payment network (see data/network_rules.json). DejaVu is never
// shown those rules: a rejection reaches it as a normal exception on the desk, exactly like a real bank's reply.
const SIM = { dir: null, presetIndex: null, dirty: false, busy: false, newCount: 0 };
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function sgtNow() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {
    timeZone: SGT, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date()).map((p) => [p.type, p.value]));
  return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}`, clock: `${parts.hour}:${parts.minute}:${parts.second}` };
}

async function initNetwork() {
  S.loaded.network = true;
  try {
    SIM.dir = await api("/api/sim/network");
  } catch (e) {
    simPanel(`<div class="note warn">The payment network simulator is not available: ${esc(e.message)}</div>`);
    return;
  }
  const opt = (v) => `<option>${esc(v)}</option>`;
  $("#sim-client").innerHTML = SIM.dir.clients.map((c) => opt(c.name)).join("");
  $("#sim-bank").innerHTML = SIM.dir.banks.map((b) => opt(b.name)).join("");
  $("#sim-inter").innerHTML = `<option value="">None</option>` + SIM.dir.intermediaries.map((b) => opt(b.name)).join("");
  const presets = S.inputs.sim_presets || [];
  $("#sim-presets").innerHTML = presets.map((p, i) => `<button class="chip" type="button" data-sim-preset="${i}">${esc(p.label)}</button>`).join("");
  $$("[data-sim-preset]").forEach((b) => (b.onclick = () => fillSimPreset(+b.dataset.simPreset)));
  const form = $("#sim-form");
  form.addEventListener("input", () => { SIM.dirty = true; });
  form.onsubmit = (e) => { e.preventDefault(); sendPayment(); };
  $("#sim-rules-btn").onclick = toggleRulebook;
  if (presets.length) fillSimPreset(0); else simDefaults();
  netLog("info", `Connected to the payment network simulator: ${SIM.dir.banks.length} banks.`);
}

function simDefaults() {
  const f = $("#sim-form");
  const now = sgtNow();
  f.elements.submit_time_sgt.value = now.time;
  f.elements.submit_date.value = now.date;
}

function fillSimPreset(i) {
  const p = S.inputs.sim_presets[i];
  const f = $("#sim-form");
  ["client", "beneficiary", "beneficiary_bank", "beneficiary_account", "currency", "amount", "intermediary", "remittance_info"].forEach((k) => {
    if (f.elements[k]) f.elements[k].value = p.v[k] !== undefined ? p.v[k] : "";
  });
  simDefaults();
  if (p.v.submit_time_sgt) f.elements.submit_time_sgt.value = p.v.submit_time_sgt;
  SIM.presetIndex = i;
  SIM.dirty = false;
  $$("[data-sim-preset]").forEach((b) => b.classList.toggle("active", +b.dataset.simPreset === i));
}

function simBody() {
  const f = $("#sim-form");
  if (!f.reportValidity()) return null;
  const body = Object.fromEntries(new FormData(f).entries());
  body.amount = parseFloat(body.amount);
  if (!body.intermediary) delete body.intermediary;
  if (!body.submit_date) body.submit_date = sgtNow().date;
  return body;
}

function simPanel(html) { $("#sim-dejavu").innerHTML = html; }

function simBusy(busy) {
  SIM.busy = busy;
  $("#sim-send").disabled = busy;
  $$("[data-sim-preset]").forEach((b) => (b.disabled = busy));
}

function netLog(kind, text, ref = "") {
  const icon = { out: "→", ok: "✓", bad: "✗", warn: "!", dv: "◆", info: "·" }[kind] || "·";
  const row = document.createElement("div");
  row.className = `net-row ${kind}`;
  row.innerHTML = `<span class="net-time mono">${sgtNow().clock}</span><span class="net-icon">${icon}</span>
    <span class="net-text">${ref ? `<span class="mono muted">${esc(ref)}</span> ` : ""}${esc(text)}</span>`;
  $("#sim-log").prepend(row);
}

function markDeskNew() {
  SIM.newCount += 1;
  const dot = $("#desk-new");
  dot.textContent = SIM.newCount;
  dot.classList.remove("hidden");
}

function clearDeskNew() {
  SIM.newCount = 0;
  $("#desk-new").classList.add("hidden");
}

function flash(el) {
  el.classList.remove("flash");
  void el.offsetWidth;  // restart the animation
  el.classList.add("flash");
}

// When a memory was formed during this session, say so ("learned 3 min ago"): that is the point of the demo.
function ageLabel(iso) {
  const t = new Date(iso).getTime();
  if (!iso || Number.isNaN(t)) return null;
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins >= -5 && mins < 240) return { text: mins <= 1 ? "learned just now" : `learned ${mins} min ago`, fresh: true };
  return { text: fmtDay(iso), fresh: false };
}

function evidenceLine(text, occurred, why, caseId) {
  const id = caseId || ((text || "").match(/EXC-\d{6}-[A-Z0-9]+/) || [])[0];
  const age = ageLabel(occurred);
  return `<div class="ev"><div class="ev-top">${id ? `<span class="badge code">${esc(id)}</span>` : ""}${age ? `<span class="badge ${age.fresh ? "teal" : ""}">${esc(age.text)}</span>` : ""}</div>
    <div class="ev-why">${esc(why || (text || "").slice(0, 200))}</div></div>`;
}

async function sendPayment(opts = {}) {
  if (SIM.busy) return;
  const body = opts.body || simBody();
  if (!body) return;
  const ref = opts.ref || `PAY-${Math.floor(1000000 + Math.random() * 9000000)}`;
  simBusy(true);
  try {
    if (!opts.skipCheck) {
      netLog("out", `New payment: ${body.client} → ${body.beneficiary} · ${fmtMoney(body.amount, body.currency)} · ${body.beneficiary_bank}`, ref);
      if ($("#sim-check").checked) {
        simPanel(thinking("DejaVu is checking this payment against everything the desk has learned…"));
        netLog("dv", "DejaVu pre-flight check…", ref);
        const r = await api("/api/precheck", { method: "POST", body });
        if (r.risk_level !== "low") {
          netLog(r.risk_level === "high" ? "bad" : "warn", `DejaVu flagged ${r.risk_level} risk and held the payment before it left the bank.`, ref);
          showBlocked(r, body, ref);
          return;
        }
        netLog("ok", "DejaVu pre-flight: low risk. Releasing the payment.", ref);
        simPanel(`<div class="sim-verdict ok">✓ Pre-flight passed</div><p class="muted">${esc(r.summary)}</p>`);
        await sleep(600);
      }
    }
    await transmit(body, ref);
  } catch (e) {
    simPanel(`<div class="note warn">${esc(e.message)}</div>`);
    netLog("bad", `Error: ${e.message}`, ref);
  } finally {
    simBusy(false);
  }
}

// Memories about the payment's own bank come first (freshest first), so the case the desk
// just learned from is what the analyst sees. Falls back to Hindsight's order otherwise.
function rankEvidence(list, bank) {
  const stop = new Set(["bank", "banking", "operative", "cooperative", "commercial", "savings", "union", "the"]);
  const key = (bank || "").toLowerCase().split(/[^a-z]+/).find((w) => w.length > 3 && !stop.has(w));
  const scored = (list || []).map((m, i) => {
    const age = ageLabel(m.occurred_start || m.mentioned_at);
    return { m, i, same: !!key && (m.text || "").toLowerCase().includes(key), fresh: age && age.fresh ? 1 : 0 };
  });
  const same = scored.filter((x) => x.same).sort((a, b) => b.fresh - a.fresh || a.i - b.i);
  return { items: (same.length ? same : scored).map((x) => x.m), sameBank: same.length > 0 };
}

function showBlocked(r, body, ref) {
  const preset = SIM.presetIndex !== null && !SIM.dirty ? (S.inputs.sim_presets || [])[SIM.presetIndex] : null;
  const fix = preset && preset.fix;
  const ranked = rankEvidence(r.evidence, body.beneficiary_bank);
  const ev = ranked.items.slice(0, 3).map((m) => evidenceLine(m.text, m.occurred_start || m.mentioned_at, "", null)).join("");
  const evTitle = ranked.sameBank ? `What it remembered about ${body.beneficiary_bank}` : "What it remembered";
  simPanel(`<div class="sim-verdict ${r.risk_level === "high" ? "bad" : "warn"}">🛡 DejaVu held this payment: ${esc(r.risk_level)} risk</div>
    <p>${esc(r.summary)}</p>
    ${warningsHtml(r)}
    ${ev ? `<div class="section"><h3>${esc(evTitle)}</h3><div class="evidence">${ev}</div></div>` : ""}
    <div class="sim-actions">
      ${fix ? `<button class="btn primary" id="sim-fix">Apply fix and send</button><span class="muted small">${esc(fix.label)}</span>` : ""}
      <button class="btn" id="sim-anyway">Send anyway</button>
      <button class="btn ghost" id="sim-cancel">Cancel</button>
    </div>`);
  if (fix) {
    $("#sim-fix").onclick = () => {
      const form = $("#sim-form");
      Object.entries(fix.set).forEach(([k, v]) => { if (form.elements[k]) { form.elements[k].value = v; flash(form.elements[k]); } });
      netLog("info", `Analyst applied the fix: ${fix.label}`, ref);
      sendPayment({ body: { ...body, ...fix.set }, ref, skipCheck: true });
    };
  }
  $("#sim-anyway").onclick = () => {
    netLog("warn", "Analyst overrode DejaVu and sent the payment anyway.", ref);
    sendPayment({ body, ref, skipCheck: true });
  };
  $("#sim-cancel").onclick = () => {
    netLog("info", "Payment cancelled before it was sent.", ref);
    simPanel(`<div class="empty small">Payment cancelled. Nothing was sent.</div>`);
  };
}

async function transmit(body, ref) {
  netLog("out", `pacs.008 sent over SWIFT to ${body.beneficiary_bank}`, ref);
  simPanel(thinking(`Payment in flight to ${body.beneficiary_bank}…`));
  const [res] = await Promise.all([api("/api/sim/send", { method: "POST", body: { ...body, payment_ref: ref } }), sleep(1400)]);
  const tail = `<div class="muted small">${esc(ref)} · ${esc(fmtMoney(body.amount, body.currency))} · ${esc(res.bank)}</div>`;
  if (res.status === "credited") {
    netLog("ok", `pacs.002 ${res.iso_status} · credited by ${res.bank}`, ref);
    simPanel(`<div class="sim-verdict ok">✓ Credited</div><p>${esc(res.message)}</p>${tail}`);
    return;
  }
  if (res.status === "credited_next_day") {
    netLog("warn", `pacs.002 ${res.iso_status} · accepted late by ${res.bank}`, ref);
    simPanel(`<div class="sim-verdict warn">Accepted, but late</div><p>${esc(res.message)}</p>${tail}`);
    return;
  }
  const why = res.reason_code ? `${res.reason_code} ${res.reason_text}` : "held for screening";
  netLog("bad", `pacs.002 ${res.iso_status} · ${why} · from ${res.bank}`, ref);
  netLog("info", `Exception ${res.case_id} opened on the desk`, ref);
  markDeskNew();
  toast(`New exception on the desk: ${res.bank}${res.reason_code ? " · " + res.reason_code : ""}`, "bad");
  await autoDiagnose(res, ref);
}

async function autoDiagnose(res, ref) {
  const head = `<div class="sim-verdict bad">✗ ${res.status === "held" ? "Held" : "Rejected"} by ${esc(res.bank)}
      ${res.reason_code ? `<span class="badge code">${esc(res.reason_code)}</span>` : ""}</div>
    <div class="what">${esc(res.message)}</div>
    <div class="muted small">Exception ${esc(res.case_id)} opened on the desk</div>`;
  simPanel(head + thinking("DejaVu is diagnosing the new exception, recalling similar cases from Hindsight…"));
  try {
    const d = await api(`/api/cases/${encodeURIComponent(res.case_id)}/diagnose`, { method: "POST", body: { use_memory: true } });
    netLog("dv", `DejaVu diagnosis: ${rcLabel(d.root_cause)} (${Math.round((d.confidence || 0) * 100)}% confident)`, ref);
    const ev = (d.evidence || []).slice(0, 3).map((e) => evidenceLine(e.text, e.occurred, e.why_relevant, e.case_id)).join("");
    simPanel(head + `<div class="sim-diag">
        <div class="muted small">DejaVu's diagnosis</div>
        <div class="sim-rc">${esc(rcLabel(d.root_cause))}<span class="sim-conf">${Math.round((d.confidence || 0) * 100)}% confident</span></div>
        <p>${esc(d.reasoning)}</p>
        <p><b>Next step:</b> ${esc(d.recommended_action)}</p>
        ${ev ? `<div class="section"><h3>Evidence from memory</h3><div class="evidence">${ev}</div></div>` : `<div class="muted small">Nothing similar in memory for this bank yet.</div>`}
        ${d.requires_human_approval ? `<div class="notes"><div class="note guard">Human approval required: DejaVu never releases this kind of hold by itself.</div></div>` : ""}
        <div class="sim-actions"><button class="btn primary" id="sim-open-desk">Review on the desk →</button></div>
      </div>`);
    $("#sim-open-desk").onclick = () => showOnDesk(res.case_id, d);
  } catch (e) {
    simPanel(head + `<div class="note warn">Diagnosis failed: ${esc(e.message)}</div>
      <div class="sim-actions"><button class="btn" id="sim-open-desk">Open on the desk →</button></div>`);
    $("#sim-open-desk").onclick = () => showOnDesk(res.case_id, null);
  }
}

// Jump to the desk with the new exception selected and the diagnosis already on screen (no second model call).
async function showOnDesk(caseId, diag) {
  clearDeskNew();
  showTab("desk");
  S.filter = "open";
  $$(".seg-btn[data-filter]").forEach((x) => x.classList.toggle("active", x.dataset.filter === "open"));
  S.cases = await api("/api/cases?status=open");
  S.cases.sort((a, b) => a.created_at.localeCompare(b.created_at));
  renderQueue();
  loadStatus();
  await selectCase(caseId);
  const item = $(`.qitem[data-id="${CSS.escape(caseId)}"]`);
  if (item) { item.scrollIntoView({ block: "center" }); flash(item); }
  if (diag && S.selectedId === caseId) {
    S.diag = diag;
    $("#diag-area").innerHTML = renderDiag(diag) + renderResolveBar(diag);
    wireDiag();
  }
}

async function toggleRulebook() {
  const box = $("#sim-rules");
  const btn = $("#sim-rules-btn");
  if (!box.classList.contains("hidden")) {
    box.classList.add("hidden");
    btn.textContent = "Reveal the hidden rulebook";
    return;
  }
  try {
    const rb = await api("/api/sim/rules");
    box.innerHTML = `<div class="muted small">${esc(rb.about)}</div>`
      + rb.banks.filter((b) => b.rules.length).map((b) => `<div class="rule"><b>${esc(b.name)}</b><div>${b.rules.map(esc).join("<br>")}</div></div>`).join("");
    box.classList.remove("hidden");
    btn.textContent = "Hide the rulebook";
  } catch (e) {
    toast(e.message, "bad");
  }
}

// ---------------------------------------------------------------- boot
async function boot() {
  $$(".tab").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  $$(".seg-btn[data-filter]").forEach((b) => (b.onclick = () => {
    S.filter = b.dataset.filter;
    $$(".seg-btn[data-filter]").forEach((x) => x.classList.toggle("active", x === b));
    loadCases();
  }));
  $("#queue-list").addEventListener("click", (e) => {
    const item = e.target.closest(".qitem");
    if (item) selectCase(item.dataset.id);
  });
  $("#reload-lessons").onclick = () => loadLessons();
  $("#refresh-playbook").onclick = async () => {
    try { await api("/api/memory/playbook/refresh", { method: "POST" }); toast("Hindsight is rewriting the playbook (about 30 seconds)"); setTimeout(loadPlaybook, 20000); }
    catch (e) { toast(e.message, "bad"); }
  };
  try {
    const res = await fetch(STATIC ? "demo_inputs.json" : "/static/demo_inputs.json");
    if (res.ok) S.inputs = await res.json();
  } catch (_) { /* suggestions and presets are optional */ }
  $("#ask-form").onsubmit = (e) => { e.preventDefault(); const q = $("#ask-input").value.trim(); if (q.length >= 3) ask(q); };
  $("#ask-suggestions").innerHTML = S.inputs.suggestions.map((q) => `<button class="chip">${esc(q)}</button>`).join("");
  $$("#ask-suggestions .chip").forEach((b) => (b.onclick = () => { $("#ask-input").value = b.textContent; ask(b.textContent); }));
  if (STATIC) {
    $("#ask-input").placeholder = "Recorded demo: pick one of the suggested questions below";
    $("#refresh-playbook").classList.add("hidden");
    // The payment network simulator needs the live app, so the recorded demo leaves it out.
    $$('[data-tab="network"], #tab-network').forEach((el) => el.remove());
  }
  $("#precheck-form").onsubmit = runPrecheck;

  document.addEventListener("keydown", (e) => {
    if (isTyping(e) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (!$("#tab-desk").classList.contains("active")) return;
    const k = e.key.toLowerCase();
    if (k === "d") diagnose();
    else if (k === "c") compare();
    else if (k === "a" && S.diag) approveDiag();
    else if (k === "j" || k === "k") {
      const i = S.cases.findIndex((c) => c.case_id === S.selectedId);
      const next = S.cases[i + (k === "j" ? 1 : -1)];
      if (next) selectCase(next.case_id);
    }
  });

  try {
    S.taxonomy = await api("/api/taxonomy");
    S.tax = Object.fromEntries(S.taxonomy.map((t) => [t.code, t]));
  } catch (_) { /* labels fall back to codes */ }
  await loadStatus();
  await loadCases();
  if (!STATIC) setInterval(loadStatus, 20000);
}

boot();
