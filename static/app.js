(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    config: { auth_required: false, max_batch: 25, max_accounts: 500 },
    accounts: [],
    results: new Map(),
    filter: "all",
    running: false,
    cancelled: false,
    token: localStorage.getItem("nf_token") || "",
  };

  const els = {
    paste: $("paste"),
    check: $("check-btn"),
    stop: $("stop-btn"),
    clear: $("clear-btn"),
    counter: $("counter"),
    bar: $("progress-bar"),
    pill: $("status-pill"),
    tokenBtn: $("token-btn"),
    modal: $("token-modal"),
    tokenInput: $("token-input"),
    body: document.querySelector("#results tbody"),
  };

  const setStatus = (text, cls) => {
    els.pill.textContent = text;
    els.pill.className = "pill" + (cls ? " " + cls : "");
  };

  const headers = () => {
    const base = { "Content-Type": "application/json" };
    if (state.token) base["X-Access-Token"] = state.token;
    return base;
  };

  const api = async (path, body) => {
    const response = await fetch(path, { method: "POST", headers: headers(), body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || "Erreur " + response.status);
      error.status = response.status;
      throw error;
    }
    return data;
  };

  const stats = () => {
    const values = [...state.results.values()];
    $("stat-total").textContent = state.accounts.length;
    $("stat-valid").textContent = values.filter((r) => r.status === "valid").length;
    $("stat-invalid").textContent = values.filter((r) => r.status === "invalid").length;
    $("stat-error").textContent = values.filter((r) => r.status === "error").length;
  };

  const rowFor = (account) => {
    const tr = document.createElement("tr");
    tr.dataset.id = String(account.id);
    tr.innerHTML = `
      <td class="mono">${account.id + 1}</td>
      <td><span class="badge pending">en attente</span></td>
      <td class="plan mono">-</td>
      <td class="country mono">-</td>
      <td class="profile mono">-</td>
      <td class="elapsed mono">-</td>
      <td><button class="ghost copy" type="button">Copier</button></td>`;
    tr.querySelector(".copy").addEventListener("click", () => copyCookies(account.cookies));
    return tr;
  };

  const paint = (result) => {
    const tr = els.body.querySelector(`tr[data-id="${result.id}"]`);
    if (!tr) return;
    const badge = tr.querySelector(".badge");
    badge.className = "badge " + result.status;
    badge.textContent = result.status === "valid" ? "valide" : result.status === "invalid" ? "invalide" : "erreur";
    tr.querySelector(".plan").textContent = result.plan || "-";
    tr.querySelector(".country").textContent = result.country || "-";
    tr.querySelector(".profile").textContent = result.profiles.length ? result.profiles.join(", ") : "-";
    tr.querySelector(".elapsed").textContent = result.elapsed_ms + " ms";
    if (result.error) tr.title = result.error;
    tr.dataset.status = result.status;
    tr.classList.add("fresh");
    applyFilter(tr);
  };

  const applyFilter = (tr) => {
    if (state.filter === "all") { tr.classList.remove("hidden"); return; }
    tr.classList.toggle("hidden", tr.dataset.status !== state.filter);
  };

  const copyCookies = async (cookies) => {
    const text = Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join("; ");
    try {
      await navigator.clipboard.writeText(text);
      setStatus("Cookies copiés", "done");
    } catch {
      setStatus("Copie impossible", "");
    }
  };

  const reset = () => {
    state.accounts = [];
    state.results.clear();
    els.body.innerHTML = "";
    els.bar.style.width = "0%";
    stats();
    setStatus("Prêt", "");
  };

  const parse = async () => {
    const text = els.paste.value;
    if (!text.trim()) { setStatus("Rien à vérifier", ""); return false; }
    const data = await api("/api/parse", { text });
    state.accounts = data.accounts;
    els.body.innerHTML = "";
    for (const account of data.accounts) els.body.appendChild(rowFor(account));
    els.counter.textContent = `${data.count} compte${data.count > 1 ? "s" : ""}`;
    stats();
    return true;
  };

  const run = async () => {
    if (state.running) return;
    if (state.config.auth_required && !state.token) { openToken(); return; }
    state.running = true;
    state.cancelled = false;
    els.check.disabled = true;
    els.stop.disabled = false;
    setStatus("Analyse...", "busy");

    try {
      const ok = await parse();
      if (!ok) return;

      const batchSize = Math.max(1, state.config.max_batch);
      let done = 0;
      for (let i = 0; i < state.accounts.length; i += batchSize) {
        if (state.cancelled) break;
        const batch = state.accounts.slice(i, i + batchSize);
        const data = await api("/api/check", { accounts: batch });
        for (const result of data.results) {
          state.results.set(result.id, result);
          paint(result);
        }
        done += batch.length;
        els.bar.style.width = Math.round((done / state.accounts.length) * 100) + "%";
        stats();
      }
      setStatus(state.cancelled ? "Arrêté" : "Terminé", state.cancelled ? "" : "done");
    } catch (error) {
      if (error.status === 401) { openToken(); setStatus("Jeton requis", ""); }
      else setStatus(error.message, "");
    } finally {
      state.running = false;
      els.check.disabled = false;
      els.stop.disabled = true;
    }
  };

  const exportResults = () => {
    const lines = [...state.results.values()].map((r) => {
      const status = r.valid ? "VALIDE" : r.status === "invalid" ? "INVALIDE" : "ERREUR";
      return `${status} | ${r.plan || "-"} | ${r.country || "-"} | ${r.profiles.join(",") || "-"}`;
    });
    if (!lines.length) { setStatus("Aucun résultat", ""); return; }
    const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "netflix-check.txt";
    link.click();
    URL.revokeObjectURL(url);
  };

  const openToken = () => {
    els.tokenInput.value = state.token;
    els.modal.classList.remove("hidden");
    els.tokenInput.focus();
  };

  const saveToken = () => {
    state.token = els.tokenInput.value.trim();
    localStorage.setItem("nf_token", state.token);
    els.modal.classList.add("hidden");
    setStatus("Jeton enregistré", "done");
  };

  const init = async () => {
    try {
      const response = await fetch("/api/version");
      const data = await response.json();
      state.config.max_batch = data.max_batch || 25;
      state.config.auth_required = !!data.auth_required;
      if (state.config.auth_required) els.tokenBtn.classList.remove("hidden");
    } catch {
      /* defaults are fine */
    }
  };

  els.check.addEventListener("click", run);
  els.stop.addEventListener("click", () => { state.cancelled = true; });
  els.clear.addEventListener("click", reset);
  $("export-btn").addEventListener("click", exportResults);
  els.tokenBtn.addEventListener("click", openToken);
  $("token-save").addEventListener("click", saveToken);
  $("token-cancel").addEventListener("click", () => els.modal.classList.add("hidden"));
  els.paste.addEventListener("input", () => {
    const count = (els.paste.value.match(/NetflixId\s*[=:]/g) || []).length;
    els.counter.textContent = `${count} compte${count > 1 ? "s" : ""}`;
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      state.filter = tab.dataset.filter;
      els.body.querySelectorAll("tr").forEach(applyFilter);
    });
  });

  init();
})();
