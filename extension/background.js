// OpenDM Integration v1.3.1 — pre-configured token
const DAEMON_URL = "http://127.0.0.1:10010/api/v1/download";
const HARDCODED_TOKEN = "837b10404be395a9ba4edb0f8f399f12e41ef45061ac4d0403099ebd31b44d60";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "opendm-download",
      title: "Download with OpenDM",
      contexts: ["link", "video", "audio"]
    });
  });
  chrome.storage.local.set({ opendm_token: HARDCODED_TOKEN, opendm_autocatch: true });
});

async function getToken() {
  const cfg = await chrome.storage.local.get(["opendm_token"]);
  return cfg.opendm_token || HARDCODED_TOKEN;
}

async function sendToDaemon(url, headers, suggestedFilename) {
  const token = await getToken();
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
    if (!res.ok) console.error("OpenDM rejected:", data);
    else console.log("OpenDM queued:", data);
  } catch (err) {
    console.error("OpenDM daemon unreachable:", err);
  }
}

async function collectHeaders(url, referrer) {
  const headers = { "User-Agent": navigator.userAgent };
  if (referrer) headers["Referer"] = referrer;
  try {
    const cookies = await chrome.cookies.getAll({ url });
    const cookieHeader = cookies.map(c => `${c.name}=${c.value}`).join("; ");
    if (cookieHeader) headers["Cookie"] = cookieHeader;
  } catch (e) {}
  return headers;
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url = info.linkUrl || info.srcUrl;
  if (!url) return;
  const headers = await collectHeaders(url, tab && tab.url);
  await sendToDaemon(url, headers, null);
});

// Auto-catch: cancel Chrome download and send to OpenDM
chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  chrome.storage.local.get(["opendm_autocatch"], async res => {
    const autocatch = (res.opendm_autocatch !== false);
    if (!autocatch) {
      suggest();
      return;
    }
    const url = item.finalUrl || item.url;
    if (!/^https?:\/\//i.test(url) || (item.mime && /^text\/html/i.test(item.mime))) {
      suggest();
      return;
    }

    chrome.downloads.cancel(item.id, () => {
      chrome.downloads.erase({ id: item.id });
    });

    const filename = item.filename ? item.filename.split(/[\\/]/).pop() : null;
    const headers = await collectHeaders(url, item.referrer || "");
    await sendToDaemon(url, headers, filename);
  });
  return true;
});
