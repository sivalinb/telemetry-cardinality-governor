"use strict";
let state = null, selectedId = null, busy = false, lastActive = null, settleUntil = 0;
const byId = id => document.getElementById(id);
const number = value => Number(value).toLocaleString();
const readable = phase => phase.replaceAll("_", " ");
const terminal = new Set(["succeeded", "no_action", "blocked", "rejected", "expired", "rolled_back", "failed", "rollback_failed"]);

async function api(path, options = {}) {
  const token = sessionStorage.getItem("governor-token") || "";
  const response = await fetch(path, {...options, headers: {"Content-Type": "application/json", ...(token ? {"Authorization": `Bearer ${token}`} : {}), ...options.headers}});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response;
}
function notify(message, error = false) {
  const notice = byId("notice");
  notice.hidden = false;
  notice.textContent = message;
  notice.classList.toggle("error", error);
}
async function act(callback) {
  if (busy) return;
  busy = true; updateControls();
  try { await callback(); await refresh(); }
  catch (error) { notify(error.message, true); }
  finally { busy = false; updateControls(); }
}
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function updateControls() {
  const report = state?.reports.find(item => item.workflow_id === selectedId);
  const approving = report?.phase === "awaiting_approval" && state?.active_workflow === selectedId;
  byId("approve-button").disabled = busy || !approving;
  byId("reject-button").disabled = busy || !approving;
  byId("scan-button").disabled = busy || Boolean(state?.active_workflow) || Date.now() < settleUntil;
  byId("export-button").disabled = busy || !report;
  document.querySelectorAll("[data-scenario]").forEach(button => button.disabled = busy || Boolean(state?.active_workflow));
}
function render() {
  byId("environment").textContent = state.mode === "lab" ? "LOCAL LABORATORY" : "OBSERVE MODE";
  byId("lab-controls").hidden = state.mode !== "lab";
  byId("monitor-toggle").checked = state.monitor;
  byId("budget-count").textContent = number(state.settings.series_budget);
  byId("connection").textContent = "Connected";
  byId("updated").textContent = "Updated " + new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
  for (const pool of ["production", "canary", "control"]) {
    const value = state.pools[pool];
    byId(pool + "-count").textContent = value.total_series === undefined ? "—" : number(value.total_series);
    byId(pool + "-health").textContent = value.healthy ? "Healthy" : "Waiting";
  }
  const production = state.pools.production, control = state.pools.control, canary = state.pools.canary;
  const breached = production.total_series > state.settings.series_budget;
  byId("production-budget").textContent = production.total_series === undefined ? "Awaiting data" : breached ? "Over budget" : "Within budget";
  byId("production-budget").classList.toggle("warning", breached);
  byId("budget-meter").style.width = Math.min(100, (production.total_series || 0) / state.settings.series_budget * 100) + "%";
  byId("budget-meter").style.backgroundColor = breached ? "#d3aa69" : "#8cbd97";
  const reduction = control.total_series ? 1 - canary.total_series / control.total_series : 0;
  byId("canary-reduction").textContent = reduction > 0.01 ? (reduction * 100).toFixed(1) + "% fewer series" : "No measured reduction";
  const chart = byId("metric-chart"); chart.replaceChildren();
  const entries = Object.entries(production.metrics || {}).sort((a,b) => b[1] - a[1]);
  if (!entries.length) chart.append(element("p", "muted", "Waiting for Prometheus to complete its first scrape."));
  for (const [name, count] of entries) {
    const row = element("div", "metric-row"), heading = element("div", "metric-row-heading");
    heading.append(element("code", "", name), element("span", "", number(count)));
    const track = element("div", "bar-track"), fill = element("div", "bar-fill" + (state.settings.allowed_drop_metrics.includes(name) ? " optional" : ""));
    fill.style.width = Math.max(1, count / Math.max(...entries.map(item => item[1])) * 100) + "%";
    track.append(fill); row.append(heading, track); chart.append(row);
  }
  if (!selectedId || !state.reports.some(item => item.workflow_id === selectedId)) selectedId = state.active_workflow || state.reports[0]?.workflow_id;
  const report = state.reports.find(item => item.workflow_id === selectedId);
  byId("phase-chip").textContent = report ? readable(report.phase) : "No active run";
  byId("phase-chip").className = "phase-chip " + (report?.phase || "");
  byId("policy-metric").textContent = report?.proposal?.metric || "A policy will appear here";
  byId("policy-reason").textContent = report?.proposal?.reason || report?.reason || "Inject a spike, then run analysis or enable automatic detection.";
  byId("policy-code").textContent = report?.proposal?.rule_yaml || "# Exact-match Prometheus drop rule\n# No configuration changes before approval";
  const labels = report?.proposal?.offending_labels || [];
  byId("label-evidence").textContent = labels.length ? labels.map(name => `${name}: ${number(report.baseline.labels[name])} distinct values in ${number(report.baseline.sampled_series)} sampled series`).join(" · ") : "";
  byId("workflow-reason").textContent = report?.reason || "Temporal records progress so a worker restart can resume the same run.";
  const seen = new Set(report?.history.map(item => item.phase) || []);
  for (const step of document.querySelectorAll("[data-step]")) {
    const phase = step.dataset.step;
    step.classList.toggle("done", seen.has(phase));
    step.classList.toggle("current", report?.phase === phase || (phase === "observing_canary" && report?.phase === "applying_canary") || (phase === "succeeded" && report?.phase === "verifying_production"));
  }
  const timeline = byId("timeline"); timeline.replaceChildren();
  for (const item of report?.history || []) {
    const point = element("span", "", new Date(item.at).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"}) + " · " + readable(item.phase));
    point.title = item.reason; timeline.append(point);
  }
  const tbody = byId("history-body"); tbody.replaceChildren();
  byId("run-count").textContent = state.reports.length + (state.reports.length === 1 ? " run" : " runs");
  if (!state.reports.length) { const row=element("tr"), cell=element("td","empty-state","Start your first analysis to see its history here.");cell.colSpan=5;row.append(cell);tbody.append(row); }
  for (const item of state.reports) {
    const row = element("tr"), idCell = element("td"), button = element("button", "history-select", item.workflow_id.slice(0, 29));
    button.title=item.workflow_id; button.addEventListener("click", () => {selectedId=item.workflow_id;render();}); idCell.append(button);
    const phaseCell=element("td");phaseCell.append(element("span","phase-chip " + item.phase,readable(item.phase)));
    const reductionValue=item.production_verification?.passed ? item.production_verification.reduction : item.verification?.passed ? item.verification.reduction : null;
    row.append(idCell,phaseCell,element("td","",item.proposal?.metric || "—"),element("td","",reductionValue===null ? "—" : (reductionValue*100).toFixed(1)+"%"),element("td","",item.updated_at ? new Date(item.updated_at).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}) : "Queued"));
    tbody.append(row);
  }
  updateControls();
}
async function refresh() {
  try {
    state=await (await api("/api/state")).json();
    if (state.active_workflow && state.active_workflow !== lastActive) selectedId=state.active_workflow;
    lastActive=state.active_workflow;render();
  }
  catch (error) { byId("connection").textContent="Unavailable";notify(error.message,true); }
}
document.querySelectorAll("[data-scenario]").forEach(button => button.addEventListener("click", () => act(async () => {
  await api("/api/lab/scenario",{method:"POST",body:JSON.stringify({scenario:button.dataset.scenario})});
  settleUntil=Date.now()+5000;setTimeout(updateControls,5100);
  notify("Scenario loaded. Allow 5 seconds for fresh scrapes before analyzing.");selectedId=null;
})));
byId("scan-button").addEventListener("click", () => act(async () => {
  const result=await (await api("/api/scans",{method:"POST",body:JSON.stringify({request_id:crypto.randomUUID()})})).json();selectedId=result.workflow_id;notify("Analysis started. Temporal is collecting evidence.");
}));
for (const [id,approved] of [["approve-button",true],["reject-button",false]]) byId(id).addEventListener("click", () => act(async () => {
  const reviewer=byId("reviewer").value.trim();if(!reviewer) throw new Error("Enter a reviewer name.");
  await api(`/api/reports/${selectedId}/decision`,{method:"POST",body:JSON.stringify({approved,reviewer,comment:approved?"Reviewed exact-match lab rule":"Rejected in dashboard"})});
  notify(approved?"Decision recorded. The canary is now running through its verification window.":"Proposal rejected. Configuration was retained.");
}));
byId("monitor-toggle").addEventListener("change", () => act(async () => {await api("/api/monitor",{method:"POST",body:JSON.stringify({enabled:byId("monitor-toggle").checked})});notify("Automatic detection " + (byId("monitor-toggle").checked?"enabled":"paused") + ".");}));
byId("export-button").addEventListener("click", () => act(async () => {
  const response=await api(`/api/reports/${selectedId}/history`);const url=URL.createObjectURL(await response.blob());const link=element("a");link.href=url;link.download=selectedId+".json";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}));
byId("token-button").addEventListener("click",()=>{byId("api-token").value=sessionStorage.getItem("governor-token")||"";byId("token-dialog").showModal();});
byId("save-token").addEventListener("click",()=>{sessionStorage.setItem("governor-token",byId("api-token").value);refresh();});
refresh();setInterval(refresh,2000);
