const api = (typeof browser !== "undefined") ? browser : chrome;

const connDot       = document.getElementById("connDot");
const connText      = document.getElementById("connText");
const taskName      = document.getElementById("taskName");
const taskSub       = document.getElementById("taskSub");
const tabTitle      = document.getElementById("tabTitle");
const tabFavicon    = document.getElementById("tabFavicon");
const relevancePill = document.getElementById("relevancePill");
const statSwitches  = document.getElementById("statSwitches");
const statLastAck   = document.getElementById("statLastAck");
const hostName      = document.getElementById("hostName");
const overridePill  = document.getElementById("overridePill");
const blockBtn      = document.getElementById("blockBtn");
const whitelistBtn  = document.getElementById("whitelistBtn");
const clearBtn      = document.getElementById("clearBtn");
const refreshBtn    = document.getElementById("refreshBtn");
const errorBar      = document.getElementById("errorBar");

function fmt(ts) {
  if (!ts) return "never";
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function elapsed(ts) {
  if (!ts) return "—";
  const s = Math.round((Date.now() - ts) / 1000);
  if (s < 60)  return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s/60)}m ago`;
  return `${Math.floor(s/3600)}h ago`;
}

async function loadStatus() {
  connText.textContent = "Checking…";
  connDot.className = "conn-dot";
  errorBar.className = "error-bar";

  try {
    const s = await api.runtime.sendMessage({ type: "get_status" });
    if (!s) { connText.textContent = "No response"; connDot.classList.add("warn"); return; }

    // Connection
    connDot.classList.add(s.connected ? "ok" : "warn");
    connText.textContent = s.connected ? "Connected to app" : "App not detected";

    // Error bar
    if (s.last_error) {
      errorBar.textContent = `Error: ${s.last_error}`;
      errorBar.classList.add("visible");
    }

    // Task
    if (s.active_session_task) {
      const t = s.active_session_task;
      taskName.textContent = t.task_title || "Untitled task";
      const kws = (t.task_keywords || []).slice(0, 4).join(", ");
      taskSub.textContent  = kws ? `Keywords: ${kws}` : "";
    } else {
      taskName.innerHTML = `<span class="no-task">No active session</span>`;
      taskSub.textContent = "";
    }

    // Current tab
    tabTitle.textContent = s.current_tab_title || "—";
    tabTitle.title       = s.current_tab_title || "";

    // Favicon from hostname
    if (s.current_host) {
      tabFavicon.src = `https://www.google.com/s2/favicons?sz=16&domain=${s.current_host}`;
      tabFavicon.style.display = "";
    } else {
      tabFavicon.style.display = "none";
    }

    // Relevance
    relevancePill.className = "relevance-pill";
    if (!s.active_session_task) {
      relevancePill.classList.add("unknown");
      relevancePill.textContent = "no task";
    } else if (s.tab_relevant_to_task === true) {
      relevancePill.classList.add("relevant");
      relevancePill.textContent = "on task";
    } else if (s.tab_relevant_to_task === false) {
      relevancePill.classList.add("offtask");
      relevancePill.textContent = "off task";
    } else {
      relevancePill.classList.add("unknown");
      relevancePill.textContent = "—";
    }

    // Stats
    statSwitches.textContent = s.tab_switch_count_5m ?? "0";
    statSwitches.className = "stat-val " + (
      (s.tab_switch_count_5m || 0) > 15 ? "danger" :
      (s.tab_switch_count_5m || 0) > 8  ? "warn"   : "accent"
    );
    statLastAck.textContent = s.last_ack_at ? elapsed(s.last_ack_at) : "—";

    // Override controls
    hostName.textContent = s.current_host || "—";
    overridePill.className = "override-pill";
    if (s.manually_blocked) {
      overridePill.classList.add("blocked"); overridePill.textContent = "blocked";
    } else if (s.manually_whitelisted) {
      overridePill.classList.add("whitelisted"); overridePill.textContent = "whitelisted";
    } else {
      overridePill.classList.add("auto"); overridePill.textContent = "auto";
    }
    blockBtn.style.display      = s.manually_blocked     ? "none" : "";
    whitelistBtn.style.display  = s.manually_whitelisted ? "none" : "";
    clearBtn.style.display      = (s.manually_blocked || s.manually_whitelisted) ? "" : "none";

  } catch (err) {
    connText.textContent = "Status failed";
    connDot.classList.add("warn");
    errorBar.textContent = err ? String(err) : "Unknown error";
    errorBar.classList.add("visible");
  }
}

async function send(type) {
  try {
    await api.runtime.sendMessage({ type });
    await loadStatus();
  } catch (err) {
    errorBar.textContent = `Action failed: ${err}`;
    errorBar.classList.add("visible");
  }
}

blockBtn.addEventListener("click",     () => send("manual_block"));
whitelistBtn.addEventListener("click", () => send("manual_whitelist"));
clearBtn.addEventListener("click",     () => send("clear_override"));
refreshBtn.addEventListener("click",   loadStatus);

loadStatus();