const $ = (id) => document.getElementById(id);
const state = { jobId: null, timer: null, filter: "all", results: [], token: localStorage.getItem("nfx_token") || "", config: null };

function headers() {
  const out = { "Content-Type": "application/json" };
  if (state.token) out["X-Access-Token"] = state.token;
  return out;
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: headers() });
  if (response.status === 401) { $("token").hidden = false; throw new Error("access token required"); }
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

function setStatus(text, kind = "") {
  $("status").textContent = text;
  $("status").style.color = kind === "error" ? "#ff6b6b" : kind === "ok" ? "#35c46a" : "";
}

async function loadConfig() {
  try {
    const config = await api("/api/config");
    state.config = config;
    if (config.auth_required) { $("token").hidden = false; $("token").value = state.token; }
    if (config.server_proxies) $("proxyInfo").textContent = `${config.server_proxies} server proxies`;
    $("concurrency").max = config.max_concurrency;
    $("concurrency").value = Math.min(config.default_concurrency, config.max_concurrency);
    $("concOut").textContent = $("concurrency").value;
    if (!config.nftoken_enabled) {
      document.querySelectorAll('input[name="device"]').forEach((el) => { el.checked = false; el.disabled = true; });
    }
  } catch (_) { setStatus("config unavailable", "error"); }
}

async function startJob() {
  const cookies = $("cookies").value.trim();
  if (!cookies) { setStatus("paste at least one cookie", "error"); return; }
  state.token = $("token").value.trim() || state.token;
  localStorage.setItem("nfx_token", state.token);
  $("run").disabled = true;
  setStatus("queued…");
  try {
    const body = {
      cookies,
      devices: [...document.querySelectorAll('input[name="device"]:checked')].map((el) => el.value),
      concurrency: Number($("concurrency").value),
      proxies: $("proxies").value.trim() || null,
    };
    const job = await api("/api/jobs", { method: "POST", body: JSON.stringify(body) });
    state.jobId = job.job_id;
    state.filter = "all";
    $("results").innerHTML = "";
    $("filters").hidden = true;
    $("progressWrap").hidden = false;
    $("stop").disabled = false;
    $("dlTxt").href = `/api/jobs/${job.job_id}/report.txt`;
    $("dlJson").href = `/api/jobs/${job.job_id}/report.json`;
    setStatus(`${job.total} account(s) queued${job.skipped ? ` (${job.skipped} skipped)` : ""}`, "ok");
    poll();
  } catch (error) { $("run").disabled = false; setStatus(error.message, "error"); }
}

async function poll() {
  if (!state.jobId) return;
  clearTimeout(state.timer);
  try {
    const job = await api(`/api/jobs/${state.jobId}`);
    render(job);
    if (["queued", "running"].includes(job.status)) {
      state.timer = setTimeout(poll, 1200);
    } else {
      $("run").disabled = false;
      $("stop").disabled = true;
      setStatus(job.status === "cancelled" ? "stopped" : "finished", "ok");
    }
  } catch (error) { setStatus(error.message, "error"); $("run").disabled = false; }
}

function rankOf(plan) { return state.config?.plans?.find((item) => item.key === plan)?.rank || 0; }
function planLabel(plan) { return state.config?.plans?.find((item) => item.key === plan)?.label || plan; }

function render(job) {
  state.results = job.results;
  $("bar").style.width = `${job.total ? Math.round((job.done / job.total) * 100) : 0}%`;
  $("progressText").textContent = `${job.done} / ${job.total}`;
  $("counters").innerHTML = ["valid", "free", "invalid", "error"]
    .map((key) => `<span>${key} <b>${job.counters[key]}</b></span>`).join("");
  renderFilters();
  renderResults();
}

function renderFilters() {
  const counts = {};
  state.results.forEach((result) => { counts[result.plan] = (counts[result.plan] || 0) + 1; });
  const plans = Object.keys(counts).sort((a, b) => rankOf(b) - rankOf(a));
  $("filters").innerHTML = ["all", ...plans]
    .map((plan) => `<button class="chip ${state.filter === plan ? "active" : ""}" data-plan="${plan}">${
      plan === "all" ? `all (${state.results.length})` : `${planLabel(plan)} (${counts[plan]})`}</button>`).join("");
  $("filters").hidden = false;
}

function renderResults() {
  const container = $("results");
  container.innerHTML = "";
  const visible = state.results.filter((result) => state.filter === "all" || result.plan === state.filter);
  if (!visible.length) { container.innerHTML = '<p class="status">no result yet</p>'; return; }
  visible.forEach((result, position) => {
    const node = $("resultTpl").content.firstElementChild.cloneNode(true);
    node.classList.add(`plan-${result.plan}`);
    node.querySelector(".rank").textContent = `#${position + 1}`;
    node.querySelector(".plan").textContent = result.plan_label;
    node.querySelector(".country").textContent = result.country || "—";
    node.querySelector(".hold").hidden = !result.on_hold;
    node.querySelector(".extra").hidden = !result.extra_member;
    node.querySelector(".email").textContent = result.email || "—";
    node.querySelector(".name").textContent = result.name || "—";
    node.querySelector(".streams").textContent = result.streams || "—";
    node.querySelector(".quality").textContent = result.quality || "—";
    node.querySelector(".price").textContent = result.price || "—";
    node.querySelector(".profiles").textContent = (result.profiles || []).join(", ") || "—";
    node.querySelector(".nftoken").textContent = result.nftoken || "—";
    node.querySelector(".expires").textContent = result.nftoken_expires || "—";
    if (result.error) { node.querySelector(".error").hidden = false; node.querySelector(".note").textContent = result.error; }
    const links = node.querySelector(".links");
    (result.links || []).forEach((link) => {
      const anchor = document.createElement("a");
      anchor.href = link.url;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.textContent = `${link.device.toUpperCase()} login`;
      links.appendChild(anchor);
    });
    node.querySelectorAll(".copy").forEach((button) => button.addEventListener("click", () => copy(button, result)));
    container.appendChild(node);
  });
}

async function copy(button, result) {
  const text = button.dataset.copy === "cookie" ? result.cookies || "" : (result.links || []).map((l) => l.url).join("\n");
  try { await navigator.clipboard.writeText(text); button.textContent = "copied"; }
  catch (_) { button.textContent = "failed"; }
  setTimeout(() => { button.textContent = button.dataset.copy === "cookie" ? "Copy cookie" : "Copy links"; }, 900);
}

async function stopJob() {
  if (!state.jobId) return;
  try { await api(`/api/jobs/${state.jobId}`, { method: "DELETE" }); setStatus("stopping…"); }
  catch (error) { setStatus(error.message, "error"); }
}

$("run").addEventListener("click", startJob);
$("stop").addEventListener("click", stopJob);
$("concurrency").addEventListener("input", (event) => { $("concOut").textContent = event.target.value; });
$("token").addEventListener("change", (event) => { state.token = event.target.value.trim(); localStorage.setItem("nfx_token", state.token); });
$("filters").addEventListener("click", (event) => {
  const chip = event.target.closest(".chip");
  if (!chip) return;
  state.filter = chip.dataset.plan;
  renderFilters();
  renderResults();
});

loadConfig();
