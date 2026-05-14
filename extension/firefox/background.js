// background.js — v2.0.0  (per-tab content-aware blocking)

const HOST_NAME            = "com.zyroo.abte";
const SNAPSHOT_INTERVAL_MS = 2000;
const SWITCH_WINDOW_MS     = 5 * 60 * 1000;
const CMD_POLL_INTERVAL_MS = 500;

// ── Expanded productive hosts list ───────────────────────────────────────────
// Pattern-matched via hostname.includes(p) — add bare domain fragments
const PRODUCTIVE_HOSTS = [
  // Dev / code
  "github.com", "gitlab.com", "bitbucket.org", "sourcehut.org",
  "codeberg.org", "gitea.io", "sr.ht",
  "stackoverflow.com", "stackexchange.com", "superuser.com", "serverfault.com",
  "askubuntu.com", "unix.stackexchange.com",
  "devdocs.io", "developer.mozilla.org", "mdn.io",
  "docs.python.org", "docs.rust-lang.org", "docs.oracle.com",
  "pkg.go.dev", "docs.rs", "cppreference.com",
  "npmjs.com", "pypi.org", "crates.io", "rubygems.org",
  "replit.com", "codesandbox.io", "codepen.io", "jsfiddle.net", "glitch.me",
  "leetcode.com", "hackerrank.com", "codewars.com", "exercism.org",
  "towardsdatascience.com", "machinelearningmastery.com",

  // Docs / knowledge
  "en.wikipedia.org", "en.wikibooks.org", "wikiversity.org",
  "docs.google.com", "drive.google.com", "sheets.google.com", "slides.google.com",
  "notion.so", "notions.so", "craft.do",
  "confluence", "atlassian.net", "jira", "trello.com", "linear.app",
  "basecamp.com", "asana.com", "monday.com", "clickup.com",
  "obsidian.md", "roamresearch.com", "logseq.com",
  "quip.com", "coda.io", "airtable.com",
  "overleaf.com", "latex.codecogs.com", "mathpix.com",
  "wolfram.com", "wolframalpha.com",
  "wolframcloud.com", "mathworld.wolfram.com",
  "arxiv.org", "semanticscholar.org", "scholar.google.com",
  "pubmed.ncbi.nlm.nih.gov", "researchgate.net", "jstor.org", "ssrn.com",
  "acm.org", "ieee.org", "springer.com", "nature.com",

  // Learning
  "coursera.org", "udemy.com", "edx.org", "khanacademy.org",
  "pluralsight.com", "linkedin.com/learning", "skillshare.com",
  "brilliant.org", "mit.edu", "ocw.mit.edu", "open.edu",
  "freecodecamp.org", "theodinproject.com", "fullstackopen.com",
  "egghead.io", "frontendmasters.com", "scrimba.com",

  // Productivity / communication
  "localhost", "127.0.0.1", "0.0.0.0",
  "slack.com", "teams.microsoft.com", "discord.com/channels",
  "meet.google.com", "zoom.us", "whereby.com", "webex.com",
  "calendar.google.com", "outlook.live.com", "calendar.apple.com",
  "todoist.com", "ticktick.com", "things.app",
  "toggl.com", "clockify.me", "harvest.com",
  "figma.com", "zeplin.io", "invisionapp.com", "framer.com",
  "lucidchart.com", "draw.io", "miro.com", "whimsical.com",
  "excalidraw.com",

  // Cloud / infra tools
  "console.aws.amazon.com", "portal.azure.com", "console.cloud.google.com",
  "vercel.com", "netlify.com", "render.com", "fly.io",
  "heroku.com", "digitalocean.com", "linode.com",
  "cloudflare.com", "sentry.io", "datadog.com",
  "grafana.com", "kibana", "prometheus",
  "postman.com", "insomnia.rest", "hoppscotch.io",
  "dockerhub.com", "hub.docker.com",

  // Writing / creative (task-dependent)
  "medium.com", "substack.com", "hashnode.com", "dev.to",
  "wordpress.com", "ghost.io",
  "grammarly.com", "hemingwayapp.com",

  // Reference / lookup
  "regex101.com", "regexr.com",
  "caniuse.com", "bundlephobia.com", "bundlejs.com",
  "json.org", "jsonlint.com",
  "jwt.io", "base64decode.org",
  "speedtest.net",
  "translate.google.com",
];

const api = (typeof browser !== "undefined") ? browser : chrome;

let nativePort     = null;
let tokenHex       = null;
let tokenPromise   = null;
const switchEvents = [];
let lastAckAt      = 0;
let lastError      = null;
let activeSessionTask = null;  // { task_title, task_keywords }
// Per-tab overrides: tabId → "blocked" | "whitelisted"
const tabOverrides = {};

// ── Script injection shim ─────────────────────────────────────────────────────
async function execScript(tabId, code) {
  if (api.scripting && api.scripting.executeScript) {
    try {
      await api.scripting.executeScript({
        target: { tabId },
        func: new Function(code),
      });
    } catch (_) {}
    return;
  }
  try { await api.tabs.executeScript(tabId, { code }); } catch (_) {}
}

// ── Tab relevance ─────────────────────────────────────────────────────────────
// Judges a single tab against the active task.
// Returns true  → tab is relevant (productive or on-task)
// Returns false → tab is off-task and should be blocked
function isTabRelevantToTask(tab) {
  if (!activeSessionTask) return true;           // No session → never block
  const { task_title = "", task_keywords = [] } = activeSessionTask;
  const url    = (tab.url    || "").toLowerCase();
  const title  = (tab.title  || "").toLowerCase();
  const haystack = `${title} ${url}`;

  // Always allow internal / privileged pages
  const NEVER_BLOCK = ["about:", "moz-extension:", "chrome:", "chrome-extension:", "edge:", "file:"];
  if (NEVER_BLOCK.some(p => url.startsWith(p))) return true;

  // Always allow explicitly productive hosts
  try {
    const host = new URL(tab.url || "").hostname.toLowerCase();
    if (PRODUCTIVE_HOSTS.some(p => host.includes(p))) return true;
  } catch (_) {}

  // Keyword match
  for (const kw of task_keywords) {
    if (kw && kw.length >= 3 && haystack.includes(kw.toLowerCase())) return true;
  }

  // Title word match (skip stop-words and short tokens)
  const STOP = new Set(["the","a","an","of","in","on","to","for","is","and","or",
                         "with","this","that","it","at","by","from","as","be","my"]);
  for (const word of task_title.toLowerCase().split(/\s+/)) {
    if (word.length >= 4 && !STOP.has(word) && haystack.includes(word)) return true;
  }

  return false;
}

// ── Block / unblock overlay ───────────────────────────────────────────────────
async function injectBlockOverlay(tabId, reason) {
  try {
    const tab = await api.tabs.get(tabId);
    if (!tab) return;
    const url = tab.url || "";
    const SKIP = ["about:", "moz-extension:", "chrome:", "chrome-extension:", "edge:", "file:"];
    if (!url || SKIP.some(p => url.startsWith(p))) return;

    await execScript(tabId, `
      (function() {
        if (document.getElementById('__abte_blocker__')) return;
        var el = document.createElement('div');
        el.id = '__abte_blocker__';
        el.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;background:#0f1512;z-index:2147483647;display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:system-ui,sans-serif;color:#ecf6f1';
        el.innerHTML = \`
          <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#3ecf8e" stroke-width="1.5">
            <circle cx="12" cy="12" r="10"/>
            <line x1="12" y1="8" x2="12" y2="12"/>
            <line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
          <h2 style="margin:20px 0 8px;font-size:22px;font-weight:600">Stay on task</h2>
          <p style="font-size:14px;color:#8fa59c;max-width:340px;text-align:center;margin:0 0 20px">
            This tab doesn't match your active focus session.<br>Switch back to your task to continue.
          </p>
          <div style="display:flex;gap:12px">
            <button onclick="document.getElementById('__abte_blocker__').remove()"
              style="background:#1e3329;border:1px solid #3ecf8e;color:#3ecf8e;padding:8px 20px;border-radius:8px;font-size:13px;cursor:pointer">
              Dismiss
            </button>
          </div>
        \`;
        document.body.appendChild(el);
      })();
    `);
  } catch (_) {}
}

async function removeBlockOverlay(tabId) {
  try {
    await execScript(tabId, `
      (function(){ var el=document.getElementById('__abte_blocker__'); if(el) el.remove(); })();
    `);
  } catch (_) {}
}

// ── Evaluate a single tab ─────────────────────────────────────────────────────
// Per-tab: each tab is judged independently by its own title + URL vs the task.
async function evaluateTab(tabId) {
  try {
    const tab = await api.tabs.get(tabId);
    if (!tab) return;

    const override = tabOverrides[tabId];
    if (override === "whitelisted")         { await removeBlockOverlay(tabId); return; }
    if (override === "blocked")             { await injectBlockOverlay(tabId, "manual"); return; }
    if (!activeSessionTask)                 { await removeBlockOverlay(tabId); return; }

    if (!isTabRelevantToTask(tab)) {
      await injectBlockOverlay(tabId, "off_task");
    } else {
      await removeBlockOverlay(tabId);
    }
  } catch (_) {}
}

// ── Evaluate ALL open tabs ─────────────────────────────────────────────────────
async function evaluateAllTabs() {
  try {
    const tabs = await api.tabs.query({});
    // Sequential to avoid hammering the DOM with injections simultaneously
    for (const tab of tabs) {
      if (tab.id != null) await evaluateTab(tab.id);
    }
  } catch (_) {}
}

// ── Handle incoming cmd_payload ───────────────────────────────────────────────
function handleCmd(cmd) {
  if (!cmd) return;

  if (cmd.cmd === "set_task") {
    activeSessionTask = {
      task_title:    cmd.task_title    || "",
      task_keywords: cmd.task_keywords || [],
    };
    evaluateAllTabs();
    return;
  }

  if (cmd.cmd === "clear_task") {
    activeSessionTask = null;
    evaluateAllTabs();
    return;
  }

  // Legacy "block" / "unblock" commands now evaluate the specific tab properly
  if (cmd.cmd === "block") {
    getActiveTab().then(tab => {
      if (!tab) return;
      tabOverrides[tab.id] = "blocked";
      injectBlockOverlay(tab.id, cmd.reason || "off_task");
    });
    return;
  }

  if (cmd.cmd === "unblock") {
    getActiveTab().then(tab => {
      if (!tab) return;
      delete tabOverrides[tab.id];
      removeBlockOverlay(tab.id);
    });
  }
}

// ── Native port management ────────────────────────────────────────────────────
function ensurePort() {
  if (nativePort) return nativePort;
  try {
    nativePort = api.runtime.connectNative(HOST_NAME);
  } catch (err) {
    lastError = err ? String(err) : "native_connect_failed";
    nativePort = null;
    return null;
  }

  nativePort.onDisconnect.addListener(() => {
    const err = api.runtime.lastError;
    if (err && err.message) lastError = err.message;
    nativePort = null;
    tokenHex = null;
    tokenPromise = null;
  });

  nativePort.onMessage.addListener((msg) => {
    if (!msg) return;

    // Token response — bootstrap task state from bridge at connect time
    if (msg.token) {
      tokenHex = msg.token;
      if (tokenPromise) { tokenPromise.resolve(msg.token); tokenPromise = null; }
    }

    if (msg.current_task !== undefined) {
      activeSessionTask = msg.current_task || null;
      evaluateAllTabs();
    }

    if (msg.ok)    { lastAckAt = Date.now(); lastError = null; }
    if (msg.error) { lastError = msg.error; }

    if (msg.cmd_payload) handleCmd(msg.cmd_payload);
  });

  return nativePort;
}

// ── Token ─────────────────────────────────────────────────────────────────────
function requestToken() {
  if (tokenHex) return Promise.resolve(tokenHex);
  if (tokenPromise) return tokenPromise.promise;
  const port = ensurePort();
  if (!port) return Promise.resolve(null);
  let resolveFn;
  const promise = new Promise(r => { resolveFn = r; });
  tokenPromise = { promise, resolve: resolveFn };
  port.postMessage({ command: "get_token" });
  return promise;
}

// ── Crypto helpers ─────────────────────────────────────────────────────────────
function hexToBytes(hex) {
  const b = new Uint8Array(hex.length / 2);
  for (let i = 0; i < b.length; i++) b[i] = parseInt(hex.substr(i*2,2),16);
  return b;
}
function bytesToBase64(b) { let s=""; for(let i=0;i<b.length;i++) s+=String.fromCharCode(b[i]); return btoa(s); }
function concatBytes(a,b) { const o=new Uint8Array(a.length+b.length); o.set(a,0); o.set(b,a.length); return o; }
async function sha256(b) { return new Uint8Array(await crypto.subtle.digest("SHA-256",b)); }
async function deriveKeystream(key,nonce,len) {
  const blocks=[]; let c=0,f=0;
  while(f<len){ const cb=new Uint8Array([0,0,0,c&0xff]); blocks.push(await sha256(concatBytes(concatBytes(key,nonce),cb))); f+=32; c++; }
  const s=new Uint8Array(len); let off=0;
  for(const bl of blocks){ if(off>=len) break; s.set(bl.subarray(0,Math.min(bl.length,len-off)),off); off+=bl.length; }
  return s;
}
async function encryptPayload(payload, keyBytes) {
  const plain=new TextEncoder().encode(JSON.stringify(payload));
  const nonce=crypto.getRandomValues(new Uint8Array(12));
  const stream=await deriveKeystream(keyBytes,nonce,plain.length);
  const cipher=plain.map((b,i)=>b^stream[i]);
  const hk=await crypto.subtle.importKey("raw",keyBytes,{name:"HMAC",hash:"SHA-256"},false,["sign"]);
  const sig=new Uint8Array(await crypto.subtle.sign("HMAC",hk,concatBytes(nonce,cipher)));
  return { nonce:bytesToBase64(nonce), payload:bytesToBase64(cipher), hmac:bytesToBase64(sig) };
}

// ── Snapshot push ─────────────────────────────────────────────────────────────
function recordSwitch() {
  const now=Date.now(); switchEvents.push(now);
  while(switchEvents.length && now-switchEvents[0]>SWITCH_WINDOW_MS) switchEvents.shift();
}
function scoreFromUrl(url) {
  if(!url) return {productive:false,score:0.45};
  let host=""; try{ host=new URL(url).hostname; }catch(_){ return {productive:false,score:0.45}; }
  const m=PRODUCTIVE_HOSTS.some(e=>host.toLowerCase().includes(e));
  return {productive:m, score:m?0.72:0.35};
}
async function getActiveTab() {
  const tabs=await api.tabs.query({active:true,lastFocusedWindow:true});
  return tabs&&tabs.length?tabs[0]:null;
}
async function buildSnapshot() {
  const tab=await getActiveTab();
  const url=tab?.url||"", title=tab?.title||"";
  const score=scoreFromUrl(url);
  return {
    active_tab_url:url, active_tab_title:title,
    is_productive:score.productive,
    tab_switch_count_5m:switchEvents.length,
    focus_score_hint:score.score,
    active_session_task:activeSessionTask,
    tab_relevant_to_task: tab ? isTabRelevantToTask(tab) : null,
  };
}
async function pushSnapshot() {
  try {
    const token=await requestToken(); if(!token) return;
    const keyBytes=hexToBytes(token);
    const enc=await encryptPayload(await buildSnapshot(),keyBytes);
    const port=ensurePort(); if(!port) return;
    port.postMessage({command:"push_state",nonce:enc.nonce,payload:enc.payload,hmac:enc.hmac});
  } catch(_) {}
}

// ── Status ─────────────────────────────────────────────────────────────────────
async function getStatus() {
  const now=Date.now();
  // FIX: lastAckAt > 0 is necessary but the 15s window is too tight on a busy machine;
  // keep it generous so the popup doesn't falsely show "disconnected".
  const connected=!!tokenHex&&lastAckAt>0&&(now-lastAckAt)<30000;
  const tab=await getActiveTab();
  const url=tab?.url||"", title=tab?.title||"";
  let host=""; try{ host=new URL(url).hostname; }catch(_){}
  const override=tabOverrides[tab?.id]||null;
  return {
    connected, last_ack_at:lastAckAt||null, last_error:lastError,
    current_host:host, current_tab_title:title,
    manually_blocked:   override==="blocked",
    manually_whitelisted: override==="whitelisted",
    tab_relevant_to_task: tab?isTabRelevantToTask(tab):null,
    active_session_task:  activeSessionTask,
    tab_switch_count_5m:  switchEvents.length,
    current_tab_id:       tab?.id||null,
  };
}

// ── Message router ─────────────────────────────────────────────────────────────
api.runtime.onMessage.addListener((msg) => {
  if(!msg) return Promise.resolve(null);
  if(msg.type==="get_status")       return getStatus();
  if(msg.type==="manual_block")     return getActiveTab().then(tab=>{
    if(!tab) return {ok:false};
    tabOverrides[tab.id]="blocked"; injectBlockOverlay(tab.id,"manual");
    return {ok:true,tabId:tab.id};
  });
  if(msg.type==="manual_whitelist") return getActiveTab().then(tab=>{
    if(!tab) return {ok:false};
    tabOverrides[tab.id]="whitelisted"; removeBlockOverlay(tab.id);
    return {ok:true,tabId:tab.id};
  });
  if(msg.type==="clear_override")   return getActiveTab().then(tab=>{
    if(!tab) return {ok:false};
    delete tabOverrides[tab.id];
    // Re-evaluate the tab properly after clearing manual override
    evaluateTab(tab.id);
    return {ok:true,tabId:tab.id};
  });
  return Promise.resolve(null);
});

// ── Tab event listeners ───────────────────────────────────────────────────────
api.tabs.onActivated.addListener(({ tabId }) => {
  recordSwitch();
  pushSnapshot();
  evaluateTab(tabId);
});

// FIX: also evaluate on "loading" status so the blocker is injected ASAP
// when navigating to an off-task URL (previously waited for "complete").
api.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  // Re-evaluate as soon as the URL is known (loading) and again when complete
  if (changeInfo.url || changeInfo.status === "complete" || changeInfo.status === "loading") {
    if (tab?.active) { recordSwitch(); pushSnapshot(); }
    evaluateTab(tabId);
  }
});

api.tabs.onCreated.addListener((tab) => {
  // A new tab should be evaluated immediately — even before it has a title
  if (tab.id != null) evaluateTab(tab.id);
});

api.tabs.onRemoved.addListener((tabId) => {
  delete tabOverrides[tabId];
});

api.windows.onFocusChanged.addListener(() => {
  recordSwitch();
  pushSnapshot();
});

// ── Module-level intervals ────────────────────────────────────────────────────
setInterval(() => {
  const port=ensurePort(); if(!port||!tokenHex) return;
  try { port.postMessage({command:"poll_cmd"}); } catch(_) {}
}, CMD_POLL_INTERVAL_MS);

setInterval(pushSnapshot, SNAPSHOT_INTERVAL_MS);

// Chrome MV3 keep-alive
if (typeof chrome !== "undefined" && chrome.alarms) {
  chrome.alarms.create("keepalive", { periodInMinutes: 0.4 });
  chrome.alarms.onAlarm.addListener(() => ensurePort());
}

ensurePort();
pushSnapshot();