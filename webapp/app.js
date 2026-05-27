const API = {
  query: "/query",
  compare: "/query/compare",
  inventory: "/inventory/search",
  health: "/health"
};

const HISTORY_KEY = "sentinelweb.history.v1";
const MAX_HISTORY = 50;
const statusSteps = [
  [0, "Opening browser..."],
  [4, "Finding the right page..."],
  [9, "Reading page content..."],
  [15, "Extracting the answer..."],
  [24, "Still working. Some sites take a minute."]
];

const els = {
  form: document.querySelector("#queryForm"),
  query: document.querySelector("#queryInput"),
  url: document.querySelector("#urlInput"),
  sites: document.querySelector("#sitesInput"),
  send: document.querySelector("#sendButton"),
  messages: document.querySelector("#messages"),
  health: document.querySelector("#healthBadge"),
  singleTab: document.querySelector("#singleTab"),
  compareTab: document.querySelector("#compareTab"),
  inventoryTab: document.querySelector("#inventoryTab"),
  compareFields: document.querySelector("#compareFields"),
  inventoryFields: document.querySelector("#inventoryFields"),
  urlDetails: document.querySelector("#urlDetails"),
  location: document.querySelector("#locationInput"),
  history: document.querySelector("#historyList"),
  clearHistory: document.querySelector("#clearHistoryButton"),
  loadingTemplate: document.querySelector("#loadingTemplate")
};

let mode = "single";
let busy = false;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function getHistory() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveHistory(item) {
  const prior = getHistory().filter((entry) => {
    return !(entry.query === item.query && entry.mode === item.mode && entry.url === item.url);
  });
  const next = [{ ...item, createdAt: new Date().toISOString() }, ...prior].slice(0, MAX_HISTORY);
  localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
  renderHistory();
}

function renderHistory() {
  const items = getHistory();
  els.history.innerHTML = "";
  if (!items.length) {
    els.history.innerHTML = '<div class="empty-history">Recent queries will appear here.</div>';
    return;
  }
  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-item";
    const modeLabel = item.mode === "inventory" ? "Inventory" : item.mode === "compare" ? "Compare" : "Single";
    button.innerHTML = `
      <div class="history-query">${escapeHtml(item.query)}</div>
      <div class="history-meta">${modeLabel}${item.url ? " - Location/URL set" : ""}</div>
    `;
    button.addEventListener("click", () => rerunHistory(item));
    els.history.append(button);
  }
}

function setMode(nextMode) {
  mode = nextMode;
  const isCompare = mode === "compare";
  const isInventory = mode === "inventory";
  els.singleTab.classList.toggle("active", !isCompare && !isInventory);
  els.compareTab.classList.toggle("active", isCompare);
  els.inventoryTab.classList.toggle("active", isInventory);
  els.singleTab.setAttribute("aria-selected", String(!isCompare && !isInventory));
  els.compareTab.setAttribute("aria-selected", String(isCompare));
  els.inventoryTab.setAttribute("aria-selected", String(isInventory));
  els.compareFields.hidden = !isCompare;
  els.inventoryFields.hidden = !isInventory;
  els.urlDetails.hidden = isCompare || isInventory;
  els.query.placeholder = isInventory
    ? "Enter product name, SKU, UPC, or model number"
    : isCompare
    ? "Compare the price and availability of SKU 12345"
    : "Check if SKU 12345 is in stock at Best Buy";
}

function addMessage(kind, html) {
  const article = document.createElement("article");
  article.className = `message ${kind}`;
  article.innerHTML = `<div class="bubble">${html}</div>`;
  els.messages.append(article);
  els.messages.scrollTop = els.messages.scrollHeight;
  return article;
}

function addUserMessage(text) {
  addMessage("user", escapeHtml(text));
}

function addLoading() {
  const node = els.loadingTemplate.content.firstElementChild.cloneNode(true);
  els.messages.append(node);
  els.messages.scrollTop = els.messages.scrollHeight;
  const started = Date.now();
  const status = node.querySelector("[data-status]");
  const elapsed = node.querySelector("[data-elapsed]");
  const timer = window.setInterval(() => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    const step = [...statusSteps].reverse().find(([at]) => seconds >= at);
    status.textContent = step ? step[1] : statusSteps[0][1];
    elapsed.textContent = `${seconds}s elapsed`;
  }, 500);
  return {
    remove() {
      window.clearInterval(timer);
      node.remove();
    }
  };
}

function metaHtml(parts) {
  const clean = parts.filter(Boolean);
  if (!clean.length) return "";
  return `<div class="meta-row">${clean.map((part) => `<span class="meta-pill">${escapeHtml(part)}</span>`).join("")}</div>`;
}

function renderSingleResult(data) {
  const answer = data.error ? `Problem: ${data.error}` : data.answer;
  const source = data.source_url ? `<a href="${escapeHtml(data.source_url)}" target="_blank" rel="noreferrer">Source</a>` : "";
  addMessage("assistant", `
    <div>${escapeHtml(answer || "No answer returned.")}</div>
    ${metaHtml([
      data.cached ? "Cached" : "",
      Number.isFinite(data.confidence) ? `Confidence ${Math.round(data.confidence * 100)}%` : "",
      data.execution_time ? `${data.execution_time}s` : "",
      data.login_used ? "Saved login used" : "",
      source
    ])}
  `);
}

function renderCompareResult(data) {
  const rows = Array.isArray(data.site_results) ? data.site_results : [];
  const table = rows.length ? `
    <table class="result-table">
      <thead><tr><th>Site</th><th>Result</th><th>Status</th><th>Time</th></tr></thead>
      <tbody>
        ${rows.map((row) => `
          <tr>
            <td data-label="Site">${escapeHtml(row.site || row.url || "Site")}</td>
            <td data-label="Result">${escapeHtml(row.answer || row.error || "No result")}</td>
            <td data-label="Status">${row.found ? "Found" : row.error ? "Error" : "Not found"}</td>
            <td data-label="Time">${escapeHtml(row.elapsed_s ?? "")}s</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  ` : "";

  addMessage("assistant", `
    <div>${escapeHtml(data.summary || "No comparison summary returned.")}</div>
    ${table}
    ${metaHtml([
      data.best_site ? `Best: ${data.best_site}` : "",
      data.elapsed_total ? `${data.elapsed_total}s total` : ""
    ])}
  `);
}

function renderInventoryResult(data) {
  const rows = Array.isArray(data.results) ? data.results : [];
  const latestProgress = Array.isArray(data.progress) ? data.progress.slice(-8) : [];
  const table = rows.length ? `
    <table class="result-table">
      <thead><tr><th>Retailer</th><th>Availability</th><th>Price</th><th>Status</th></tr></thead>
      <tbody>
        ${rows.map((row) => `
          <tr>
            <td data-label="Retailer">${escapeHtml(row.provider || "Retailer")}</td>
            <td data-label="Availability">${escapeHtml(row.availability || row.error || "No availability returned")}</td>
            <td data-label="Price">${escapeHtml(row.price || "")}</td>
            <td data-label="Status">${escapeHtml(row.status || "")}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  ` : "";
  const progress = latestProgress.length ? `
    <ul class="progress-list">
      ${latestProgress.map((item) => `<li>${escapeHtml([item.provider, item.state, item.detail].filter(Boolean).join(" - "))}</li>`).join("")}
    </ul>
  ` : "";

  addMessage("assistant", `
    <div>${data.status === "completed" ? "Inventory lookup completed." : "Inventory lookup unavailable."}</div>
    ${table}
    ${progress}
    ${metaHtml([
      data.cache_hit ? "Cached" : "",
      data.execution_time ? `${data.execution_time}s` : ""
    ])}
  `);
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    credentials: "same-origin",
    cache: "no-store"
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || data.error || `Request failed with ${response.status}`);
  }
  return data;
}

function parseSites(value) {
  return value
    .split(/\r?\n|,/)
    .map((site) => site.trim())
    .filter(Boolean);
}

function selectedInventoryProviders() {
  return [...document.querySelectorAll('input[name="inventoryProvider"]:checked')]
    .map((input) => input.value);
}

async function pollInventory(searchId, loading) {
  for (;;) {
    await new Promise((resolve) => window.setTimeout(resolve, 1200));
    const response = await fetch(`${API.inventory}/${encodeURIComponent(searchId)}`, {
      credentials: "same-origin",
      cache: "no-store"
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || data.error || `Inventory poll failed with ${response.status}`);
    }
    if (data.status === "completed" || data.status === "unavailable") {
      loading.remove();
      renderInventoryResult(data);
      return data;
    }
  }
}

async function submitQuery(event) {
  event.preventDefault();
  if (busy) return;
  const query = els.query.value.trim();
  const url = els.url.value.trim();
  const sites = parseSites(els.sites.value);
  if (!query) return;
  if (mode === "compare" && !sites.length) {
    addMessage("assistant", "Add at least one site URL for compare mode.");
    els.sites.focus();
    return;
  }

  busy = true;
  els.send.disabled = true;
  addUserMessage(query);
  const loading = addLoading();

  try {
    if (mode === "compare") {
      const data = await postJson(API.compare, { query, sites, headless: true });
      loading.remove();
      renderCompareResult(data);
      saveHistory({ mode, query, sites, url: "" });
    } else if (mode === "inventory") {
      const providers = selectedInventoryProviders();
      if (!providers.length) {
        throw new Error("Select at least one retailer.");
      }
      const data = await postJson(API.inventory, {
        product: query,
        location: els.location.value.trim(),
        providers
      });
      if (data.cache_hit && data.result) {
        loading.remove();
        renderInventoryResult(data.result);
      } else {
        await pollInventory(data.search_id, loading);
      }
      saveHistory({ mode, query, url: els.location.value.trim(), sites: providers });
    } else {
      const payload = { query, headless: true };
      if (url) payload.url = url;
      const data = await postJson(API.query, payload);
      loading.remove();
      renderSingleResult(data);
      saveHistory({ mode, query, url, sites: [] });
    }
  } catch (error) {
    loading.remove();
    addMessage("assistant", `Request failed: ${escapeHtml(error.message)}`);
  } finally {
    busy = false;
    els.send.disabled = false;
    els.query.value = "";
    autosize(els.query);
  }
}

function rerunHistory(item) {
  setMode(item.mode === "compare" ? "compare" : item.mode === "inventory" ? "inventory" : "single");
  els.query.value = item.query || "";
  els.url.value = item.url || "";
  els.location.value = item.mode === "inventory" ? item.url || "" : "";
  els.sites.value = Array.isArray(item.sites) ? item.sites.join("\n") : "";
  if (item.mode === "inventory" && Array.isArray(item.sites)) {
    document.querySelectorAll('input[name="inventoryProvider"]').forEach((input) => {
      input.checked = item.sites.includes(input.value);
    });
  }
  autosize(els.query);
  els.form.requestSubmit();
}

function autosize(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 150)}px`;
}

async function checkHealth() {
  try {
    const response = await fetch(API.health, { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error("Health check failed");
    const data = await response.json();
    els.health.classList.remove("bad");
    els.health.classList.add("ok");
    els.health.querySelector("span:last-child").textContent = data.ollama_connected ? "Ready" : "Pattern mode";
  } catch {
    els.health.classList.remove("ok");
    els.health.classList.add("bad");
    els.health.querySelector("span:last-child").textContent = "Offline";
  }
}

els.form.addEventListener("submit", submitQuery);
els.singleTab.addEventListener("click", () => setMode("single"));
els.compareTab.addEventListener("click", () => setMode("compare"));
els.inventoryTab.addEventListener("click", () => setMode("inventory"));
els.clearHistory.addEventListener("click", () => {
  localStorage.removeItem(HISTORY_KEY);
  renderHistory();
});
els.query.addEventListener("input", () => autosize(els.query));
els.query.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.form.requestSubmit();
  }
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("./sw.js").catch(() => {});
  });
}

setMode("single");
renderHistory();
checkHealth();
window.setInterval(checkHealth, 60000);
