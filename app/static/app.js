const $ = (id) => document.getElementById(id);
const state = { jobId: null, timer: null, filter: "all", results: [], picked: [], token: localStorage.getItem("nfx_token") || "", config: null };

const rows = new Map();
let frame = 0;

const setText = (node, value) => {
  const text = value == null || value === "" ? "—" : String(value);
  if (node.textContent !== text) node.textContent = text;
};

function authHeaders() {
  return state.token ? { "X-Access-Token": state.token } : {};
}

async function send(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...authHeaders(), ...(options.headers || {}) } });
  if (response.status === 401) { $("token").hidden = false; throw new Error("access token required"); }
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

const api = (path, body) => send(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function setStatus(text, kind = "") {
  $("status").textContent = text;
  $("status").style.color = kind === "error" ? "#ff6b6b" : kind === "ok" ? "#35c46a" : "";
}

async function loadConfig() {
  try {
    const config = await send("/api/config");
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

function addFiles(fileList) {
  const max = state.config?.max_files || 10000;
  let dropped = 0;
  [...fileList].forEach((file) => {
    if (state.picked.length >= max) { dropped += 1; return; }
    const name = file.name.toLowerCase();
    if (!name.endsWith(".txt") && !name.endsWith(".json") && !name.endsWith(".zip") && !/text|json|zip/.test(file.type)) return;
    if (state.picked.some((item) => item.name === file.name && item.size === file.size)) return;
    state.picked.push(file);
  });
  renderFiles();
  if (dropped) setStatus(`${dropped} file(s) ignored, limit is ${max}`, "error");
}

function renderFiles() {
  $("fileList").innerHTML = "";
  $("fileCount").textContent = state.picked.length ? `${state.picked.length} file(s) queued` : "";
  const shown = state.picked.slice(0, 40);
  const fragment = document.createDocumentFragment();
  shown.forEach((file) => {
    const chip = document.createElement("div");
    chip.className = "file-chip";
    const label = document.createElement("span");
    label.textContent = file.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      state.picked = state.picked.filter((item) => item !== file);
      renderFiles();
    });
    chip.append(label, remove);
    fragment.appendChild(chip);
  });
  if (state.picked.length > shown.length) {
    const more = document.createElement("div");
    more.className = "file-chip";
    more.textContent = `+${state.picked.length - shown.length} more`;
    fragment.appendChild(more);
  }
  $("fileList").appendChild(fragment);
}

async function startJob() {
  const cookies = $("cookies").value.trim();
  if (!state.picked.length && !cookies) { setStatus("add a cookie file or paste cookies", "error"); return; }
  state.token = $("token").value.trim() || state.token;
  localStorage.setItem("nfx_token", state.token);
  $("run").disabled = true;
  setStatus("uploading…");
  try {
    const devices = [...document.querySelectorAll('input[name="device"]:checked')].map((el) => el.value);
    const concurrency = Number($("concurrency").value);
    const proxies = $("proxies").value.trim() || null;
    let job;
    if (state.picked.length) {
      const form = new FormData();
      state.picked.forEach((file) => form.append("files", file));
      if (cookies) form.append("cookies", cookies);
      form.append("devices", devices.join(","));
      form.append("concurrency", String(concurrency));
      if (proxies) form.append("proxies", proxies);
      job = await send("/api/jobs/files", { method: "POST", body: form });
    } else {
      job = await api("/api/jobs", { cookies, devices, concurrency, proxies });
    }
    resetResults();
    state.jobId = job.job_id;
    $("progressWrap").hidden = false;
    $("stop").disabled = false;
    $("dlTxt").href = `/api/jobs/${job.job_id}/report.txt`;
    $("dlJson").href = `/api/jobs/${job.job_id}/report.json`;
    setStatus(`${job.total} account(s) queued${job.skipped ? ` (${job.skipped} skipped)` : ""}`, "ok");
    poll();
  } catch (error) { $("run").disabled = false; setStatus(error.message, "error"); }
}

function resetResults() {
  state.results = [];
  state.filter = "all";
  rows.clear();
  $("results").innerHTML = "";
  $("filters").hidden = true;
}

async function poll() {
  if (!state.jobId) return;
  clearTimeout(state.timer);
  try {
    const job = await send(`/api/jobs/${state.jobId}?limit=300`);
    render(job);
    if (["queued", "running"].includes(job.status)) {
      state.timer = setTimeout(poll, 1500);
    } else {
      render(await send(`/api/jobs/${state.jobId}?limit=5000`));
      $("run").disabled = false;
      $("stop").disabled = true;
      setStatus(job.status === "cancelled" ? "stopped" : "finished", "ok");
    }
  } catch (error) { setStatus(error.message, "error"); $("run").disabled = false; }
}

const rankOf = (plan) => state.config?.plans?.find((item) => item.key === plan)?.rank || 0;
const planLabel = (plan) => state.config?.plans?.find((item) => item.key === plan)?.label || plan;

function render(job) {
  state.results = job.results;
  $("bar").style.width = `${job.total ? Math.round((job.done / job.total) * 100) : 0}%`;
  $("progressText").textContent = `${job.done} / ${job.total}`;
  $("shownInfo").textContent = job.total > job.results.length ? `top ${job.results.length} shown` : "";
  $("counters").innerHTML = ["valid", "free", "invalid", "error"]
    .map((key) => `<span>${key} <b>${job.counters[key]}</b></span>`).join("");
  renderFilters();
  scheduleRows();
}

function renderFilters() {
  const counts = {};
  state.results.forEach((result) => { counts[result.plan] = (counts[result.plan] || 0) + 1; });
  const plans = Object.keys(counts).sort((a, b) => rankOf(b) - rankOf(a));
  $("filters").innerHTML = ["all", ...plans]
    .map((plan) => `<button class="chip ${state.filter === plan ? "active" : ""}" data-plan="${plan}">${
      plan === "all" ? `all (${state.results.length})` : `${planLabel(plan)} (${counts[plan]})`}</button>`).join("");
  $("filters").hidden = !plans.length;
}

function buildRow(result) {
  const node = $("resultTpl").content.firstElementChild.cloneNode(true);
  node.dataset.index = String(result.index);
  node.querySelectorAll(".copy").forEach((button) => button.addEventListener("click", () => copy(button, result)));
  return node;
}

function paint(node, result, position) {
  node.className = `result plan-${result.plan}`;
  setText(node.querySelector(".rank"), `#${position + 1}`);
  const badge = node.querySelector(".plan");
  setText(badge, result.plan_label);
  badge.className = `badge plan plan-${result.plan}`;
  setText(node.querySelector(".country"), result.country);
  setText(node.querySelector(".source"), result.source);
  node.querySelector(".hold").hidden = !result.on_hold;
  node.querySelector(".extra").hidden = !result.extra_member;
  setText(node.querySelector(".email"), result.email);
  setText(node.querySelector(".name"), result.name);
  setText(node.querySelector(".streams"), result.streams);
  setText(node.querySelector(".quality"), result.quality);
  setText(node.querySelector(".price"), result.price);
  setText(node.querySelector(".profiles"), (result.profiles || []).join(", "));
  setText(node.querySelector(".nftoken"), result.nftoken);
  setText(node.querySelector(".expires"), result.nftoken_expires);
  node.querySelector(".error").hidden = !result.error;
  if (result.error) setText(node.querySelector(".note"), result.error);

  const links = node.querySelector(".links");
  const signature = (result.links || []).map((link) => link.device).join(",");
  if (links.dataset.signature !== signature) {
    links.dataset.signature = signature;
    links.innerHTML = "";
    (result.links || []).forEach((link) => {
      const anchor = document.createElement("a");
      anchor.href = link.url;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.textContent = `${link.device.toUpperCase()} login`;
      links.appendChild(anchor);
    });
  }
}

function scheduleRows() {
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    paintRows();
  });
}

function paintRows() {
  const container = $("results");
  const visible = state.results.filter((result) => state.filter === "all" || result.plan === state.filter);
  const keep = new Set();

  if (!visible.length) {
    if (!container.querySelector(".empty")) {
      container.innerHTML = '<p class="empty">no result yet</p>';
      rows.clear();
    }
    return;
  }
  const empty = container.querySelector(".empty");
  if (empty) empty.remove();

  visible.forEach((result, position) => {
    keep.add(result.index);
    let node = rows.get(result.index);
    if (!node) {
      node = buildRow(result);
      rows.set(result.index, node);
    }
    paint(node, result, position);
    container.appendChild(node);
  });

  rows.forEach((node, index) => {
    if (!keep.has(index)) { node.remove(); rows.delete(index); }
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
  try { await send(`/api/jobs/${state.jobId}`, { method: "DELETE" }); setStatus("stopping…"); }
  catch (error) { setStatus(error.message, "error"); }
}

$("run").addEventListener("click", startJob);
$("stop").addEventListener("click", stopJob);
$("browse").addEventListener("click", () => $("files").click());
$("files").addEventListener("change", (event) => { addFiles(event.target.files); event.target.value = ""; });
$("clear").addEventListener("click", () => { state.picked = []; renderFiles(); });
$("concurrency").addEventListener("input", (event) => { $("concOut").textContent = event.target.value; });
$("token").addEventListener("change", (event) => { state.token = event.target.value.trim(); localStorage.setItem("nfx_token", state.token); });
$("filters").addEventListener("click", (event) => {
  const chip = event.target.closest(".chip");
  if (!chip) return;
  state.filter = chip.dataset.plan;
  renderFilters();
  scheduleRows();
});

const drop = $("drop");
["dragenter", "dragover"].forEach((type) => drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((type) => drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (event) => { if (event.dataTransfer?.files) addFiles(event.dataTransfer.files); });
drop.addEventListener("click", (event) => { if (event.target === drop) $("files").click(); });

loadConfig();
