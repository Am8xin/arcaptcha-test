/* Plain-JS client of the REST API. No build step, no framework. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const STATUS_CLASS = {
  SUCCESS: "good", FAILED: "bad", BLOCKED: "warn",
  TIMEOUT: "warn", CANCELLED: "info", RUNNING: "run", PENDING: "muted",
};

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 4200);
}

function fmtDuration(run) {
  if (!run.started_at || !run.ended_at) return "—";
  const ms = new Date(run.ended_at) - new Date(run.started_at);
  if (ms < 0) return "—";
  const s = Math.round(ms / 1000);
  return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
}

async function api(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body;
}

/* ---------- global stats ---------- */
async function refreshStats() {
  try {
    const stats = await api("/api/stats");
    const total = Object.values(stats).reduce((a, b) => a + b, 0);
    $("#global-stats").innerHTML =
      `<span>runs: <b>${total}</b></span>` +
      `<span style="color:var(--green)">✓ ${stats.SUCCESS || 0}</span>` +
      `<span style="color:var(--red)">✗ ${(stats.FAILED || 0) + (stats.TIMEOUT || 0)}</span>` +
      `<span style="color:var(--orange)">⛨ ${stats.BLOCKED || 0}</span>`;
  } catch { /* page may not have the stats node */ }
}

/* ---------- bots page ---------- */
function fieldInput(field) {
  const input = field.secret
    ? `<input class="cfg-input" type="password" name="${esc(field.name)}" autocomplete="off">`
    : `<input class="cfg-input" type="text" name="${esc(field.name)}" value="${esc(field.default)}">`;
  return `<label class="field-label">${esc(field.label)}${field.required ? " *" : ""}
    ${input}<span class="mode-hint">${esc(field.help)}</span></label>`;
}

/* ---------- credentials table (launch dialog) ---------- */
const CRED_FIELD_NAMES = new Set(["username", "password"]);

function addCredRow() {
  const row = document.createElement("div");
  row.className = "cred-row";
  row.innerHTML = `
    <input class="cfg-input" type="text" placeholder="username" data-cred="username" autocomplete="off">
    <input class="cfg-input" type="password" placeholder="password" data-cred="password" autocomplete="new-password">
    <button type="button" class="cred-remove" title="Remove this pair">&times;</button>`;
  row.querySelector(".cred-remove").addEventListener("click", () => {
    row.remove();
    if (!$("#cred-rows").children.length) addCredRow();  // always keep one row
  });
  $("#cred-rows").appendChild(row);
}

function collectCredentials() {
  const rows = $$("#cred-rows .cred-row");
  const pairs = rows.map((row) => ({
    username: row.querySelector('[data-cred="username"]').value.trim(),
    password: row.querySelector('[data-cred="password"]').value.trim(),
  }));
  const partial = rows.find((_, i) =>
    (pairs[i].username ? 1 : 0) + (pairs[i].password ? 1 : 0) === 1);
  if (partial) {
    return { error: "Each pair needs both a username and a password — " +
                    "or clear the row to use the bot defaults." };
  }
  return { pairs: pairs.filter((p) => p.username || p.password) };
}

function openLaunchDialog(bot) {
  $("#launch-title").textContent = `Launch: ${bot.title}`;
  $("#launch-desc").textContent = bot.description;
  const credCapable = bot.config_fields.some((f) => CRED_FIELD_NAMES.has(f.name));
  $("#credentials-section").classList.toggle("hidden", !credCapable);
  $("#launch-fields").innerHTML = bot.config_fields
    .filter((f) => !(credCapable && CRED_FIELD_NAMES.has(f.name)))
    .map(fieldInput).join("");
  $("#cred-rows").innerHTML = "";
  if (credCapable) addCredRow();
  const dialog = $("#launch-dialog");
  dialog.dataset.bot = bot.name;
  dialog.showModal();
}

async function submitLaunch(event) {
  event.preventDefault();
  const dialog = $("#launch-dialog");
  const form = $("#launch-form");
  const mode = form.elements.mode.value;
  const repeat = Math.max(1, Math.min(50, parseInt(form.elements.repeat.value, 10) || 1));
  const config = {};
  $$("#launch-fields input", form).forEach((el) => { config[el.name] = el.value; });
  const creds = collectCredentials();
  if (creds.error) { toast(creds.error, true); return; }
  const button = $("#launch-submit");
  button.disabled = true;
  try {
    const res = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bot: dialog.dataset.bot, mode, config, repeat,
                             credentials: creds.pairs }),
    });
    dialog.close();
    const ids = res.run_ids && res.run_ids.length ? res.run_ids : [res.run_id];
    const first = ids[0];
    if (ids.length > 1) {
      toast(`${ids.length} runs launched — check the history page to follow them`);
      setTimeout(() => { window.location.href = "/runs"; }, 700);
    } else {
      toast(`Run launched: ${first.slice(0, 8)} — open history to follow it`);
      setTimeout(() => { window.location.href = `/runs/${first}`; }, 700);
    }
  } catch (err) {
    toast(`Launch failed: ${err.message}`, true);
  } finally {
    button.disabled = false;
  }
}

async function loadBots() {
  const bots = await api("/api/bots");
  const wrap = $("#bots");
  if (!bots.length) {
    wrap.innerHTML = '<div class="loading">No bots registered yet.</div>';
    return;
  }
  wrap.innerHTML = bots.map((bot) => `
    <article class="bot-card" data-bot="${esc(bot.name)}">
      <span class="engine">${esc(bot.engine)}</span>
      <h3>${esc(bot.title)}</h3>
      <p>${esc(bot.description)}</p>
      <div class="card-foot">
        <span class="muted">${bot.config_fields.length} config field(s)</span>
        <button class="btn primary launch-btn">Launch run</button>
      </div>
    </article>`).join("");
  $$(".launch-btn", wrap).forEach((btn) => {
    btn.addEventListener("click", () => {
      const bot = bots.find((b) => b.name === btn.closest(".bot-card").dataset.bot);
      openLaunchDialog(bot);
    });
  });
}

/* ---------- history page ---------- */
function fillBotFilter(runs) {
  const select = $("#filter-bot");
  const known = new Set([...select.options].map((o) => o.value));
  runs.forEach((r) => {
    if (!known.has(r.bot)) {
      known.add(r.bot);
      select.add(new Option(r.bot, r.bot));
    }
  });
}

function renderRuns(runs) {
  const tbody = $("#runs-table tbody");
  $("#runs-count").textContent = `${runs.length} run(s)`;
  if (!runs.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="loading">No runs yet — launch a bot from the Bots page.</td></tr>';
    return;
  }
  tbody.innerHTML = runs.map((run) => `
    <tr data-id="${esc(run.id)}">
      <td><code>${esc(run.id.slice(0, 8))}</code></td>
      <td>${esc(run.bot)}</td>
      <td>${esc(run.mode)}</td>
      <td><span class="badge ${esc(run.status)}">${esc(run.status)}</span></td>
      <td>${esc(run.started_at || "—")}</td>
      <td>${fmtDuration(run)}</td>
      <td><code>${esc(run.challenge_id || "—")}</code></td>
      <td class="cell-error">${esc(run.error || "")}</td>
    </tr>`).join("");
  $$("tbody tr", $("#runs-table")).forEach((tr) =>
    tr.addEventListener("click", () => { window.location.href = `/runs/${tr.dataset.id}`; }));
}

async function loadRuns() {
  const bot = $("#filter-bot").value;
  const status = $("#filter-status").value;
  const params = new URLSearchParams({ limit: "100" });
  if (bot) params.set("bot", bot);
  if (status) params.set("status", status);
  const runs = await api(`/api/runs?${params}`);
  fillBotFilter(runs);
  renderRuns(runs);
}

/* ---------- run detail page ---------- */
function kvRows(pairs) {
  return pairs.map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td>${v == null ? "—" : esc(v)}</td></tr>`).join("");
}

async function loadRunDetail(runId) {
  let run;
  try {
    run = await api(`/api/runs/${runId}`);
  } catch {
    $("#run-log").textContent = "Run not found.";
    return;
  }
  $("#run-status").textContent = run.status;
  $("#run-status").className = `badge ${run.status}`;

  $("#run-kv tbody").innerHTML = kvRows([
    ["Run ID", run.id],
    ["Bot", run.bot],
    ["Mode", run.mode],
    ["Started at", run.started_at],
    ["Ended at", run.ended_at],
    ["Duration", fmtDuration(run)],
    ["challenge_id", run.challenge_id],
    ["Error", run.error],
  ]);

  const d = run.details || {};
  $("#run-details tbody").innerHTML = kvRows([
    ["Captcha solved", d.captcha_solved],
    ["Captcha token present", d.captcha_token_present],
    ["Login verified", d.verified],
    ["Login HTTP status", d.login_http_status],
    ["Login response", d.login_response],
    ["Config", JSON.stringify(run.config)],
  ]);

  const actions = $("#run-actions");
  actions.innerHTML = "";
  if (["PENDING", "RUNNING"].includes(run.status)) {
    const btn = document.createElement("button");
    btn.className = "btn danger";
    btn.textContent = "Cancel run";
    btn.addEventListener("click", async () => {
      try {
        await api(`/api/runs/${run.id}/cancel`, { method: "POST" });
        toast("Cancellation requested");
        loadRunDetail(runId);
      } catch (err) { toast(err.message, true); }
    });
    actions.append(btn);
  }

  $("#run-log").textContent = (run.log || []).join("\n") || "(no log output yet)";

  if (["PENDING", "RUNNING"].includes(run.status)) {
    setTimeout(() => loadRunDetail(runId), 2000);
  }
}

/* ---------- page bootstrap ---------- */
document.addEventListener("DOMContentLoaded", () => {
  refreshStats();
  setInterval(refreshStats, 5000);

  const page = document.body.dataset.page;

  if (page === "bots") {
    loadBots().catch((e) => toast(e.message, true));
    $("#launch-cancel").addEventListener("click", () => $("#launch-dialog").close());
    $("#launch-form").addEventListener("submit", submitLaunch);
    $("#cred-add").addEventListener("click", () => addCredRow());
  } else if (page === "runs") {
    $("#filter-bot").addEventListener("change", loadRuns);
    $("#filter-status").addEventListener("change", loadRuns);
    loadRuns().catch((e) => toast(e.message, true));
    setInterval(() => loadRuns().catch(() => {}), 3000);
  } else if (page === "run") {
    const runId = window.location.pathname.split("/").pop();
    loadRunDetail(runId).catch((e) => toast(e.message, true));
  }
});
