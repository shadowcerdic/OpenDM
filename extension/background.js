// OpenDM Integration v1.2.0 — multi-connection downloads via local daemon
// Features: context-menu download, optional auto-catch of browser downloads,
// token auth, per-URL cookies + referer forwarding.

const DAEMON_URL = "http://127.0.0.1:10010/api/v1/download";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "opendm-download",
      title: "Download with OpenDM (multi-connection)",
      contexts: ["link", "video", "audio"]
    });
    chrome.contextMenus.create({
      id: "opendm-autocatch-toggle",
      type: "checkbox",
      checked: false,
      title: "Auto-catch browser downloads",
      contexts: ["action"]
    });
  });
  chrome.storage.local.get(["opendm_autocatch"], res => {
    if (res.opendm_autocatch === undefined) {
      chrome.storage.local.set({ opendm_autocatch: false });
    }
  });
});

async function getToken() {
  const cfg = await chrome.storage.local.get(["opendm_token"]);
  return cfg.opendm_token || "";
}

async function sendToDaemon(url, headers, suggestedFilename) {
  const token = await getToken();
  if (!token) {
    console.warn("OpenDM: token not configured — opening options.");
    chrome.runtime.openOptionsPage();
    return;
  }
  const payload = {
    url,
    connections: 4,
    headers,
    ...(suggestedFilename ? { suggested_filename: suggestedFilename } : {})
  };
  try {
    const res = await fetch(DAEMON_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-OpenDM-Token": token },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) console.error("OpenDM daemon rejected task:", data);
    else console.log("OpenDM queued job:", data);
  } catch (err) {
    console.error("OpenDM daemon unreachable (is the app running?):", err);
  }
}

async function collectHeaders(url, referrer) {
  const headers = { "User-Agent": navigator.userAgent };
  if (referrer) headers["Referer"] = referrer;
  try {
    const cookies = await chrome.cookies.getAll({ url });
    const cookieHeader = cookies.map(c => `${c.name}=${c.value}`).join("; ");
    if (cookieHeader) headers["Cookie"] = cookieHeader;
  } catch (e) { /* cookies permission optional */ }
  return headers;
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === "opendm-autocatch-toggle") {
    const cfg = await chrome.storage.local.get(["opendm_autocatch"]);
    await chrome.storage.local.set({ opendm_autocatch: !cfg.opendm_autocatch });
    return;
  }
  const url = info.linkUrl || info.srcUrl;
  if (!url) return;
  const headers = await collectHeaders(url, tab && tab.url);
  await sendToDaemon(url, headers, null);
});

// ---- Auto-catch: intercept browser downloads and hand them to OpenDM ----
chrome.downloads.onCreated.addListener(async (item) => {
  const cfg = await chrome.storage.local.get(["opendm_autocatch"]);
  if (!cfg.opendm_autocatch) return;

  const url = item.finalUrl || item.url;
  if (!/^https?:\/\//i.test(url)) return;          // only http/https
  if (item.mime && /^text\/html/i.test(item.mime)) return; // pages, not files

  try {
    await chrome.downloads.cancel(item.id);
    await chrome.downloads.erase({ id: item.id });
  } catch (e) { /* already gone */ }

  let suggested = null;
  if (item.filename) {
    suggested = item.filename.split(/[\\/]/).pop();
  }
  const headers = await collectHeaders(url, item.referrer || "");
  await sendToDaemon(url, headers, suggested);
});
