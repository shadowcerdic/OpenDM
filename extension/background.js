// OpenDM Integration v2.0.0 — secure Native Messaging token bootstrapping
const DEFAULT_DAEMON_URL = "http://127.0.0.1:10010/api/v1/download";
const NATIVE_HOST_NAME = "org.opendm.native_host";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "opendm-download",
      title: "Download with OpenDM",
      contexts: ["link", "video", "audio"]
    });
  });
  chrome.storage.local.get(["opendm_autocatch"], res => {
    if (res.opendm_autocatch === undefined) {
      chrome.storage.local.set({ opendm_autocatch: true });
    }
  });
});

/**
 * Bootstrap session token securely:
 * 1. Checks storage cache.
 * 2. If missing, requests via Chrome Native Messaging (stdin/stdout) without exposing secrets over HTTP.
 */
async function getToken() {
  const cfg = await chrome.storage.local.get(["opendm_token"]);
  if (cfg.opendm_token) {
    return cfg.opendm_token;
  }

  // Attempt Native Messaging handshake
  try {
    const response = await new Promise((resolve, reject) => {
      chrome.runtime.sendNativeMessage(NATIVE_HOST_NAME, { cmd: "get_token" }, (res) => {
        if (chrome.runtime.lastError) {
          reject(chrome.runtime.lastError);
        } else {
          resolve(res);
        }
      });
    });

    if (response && response.ok && response.token) {
      await chrome.storage.local.set({ opendm_token: response.token });
      return response.token;
    }
  } catch (err) {
    console.debug("Native messaging not available or failed:", err);
  }

  return "";
}

async function sendToDaemon(url, headers, suggestedFilename) {
  const token = await getToken();
  const payload = {
    url,
    connections: 4,
    headers,
    show_dialog: true,
    ...(suggestedFilename ? { suggested_filename: suggestedFilename } : {})
  };

  try {
    const res = await fetch(DEFAULT_DAEMON_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { "X-OpenDM-Token": token } : {})
      },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) {
      if (res.status === 401) {
        // Token was invalid or rotated; clear local cache to trigger refresh
        await chrome.storage.local.remove(["opendm_token"]);
      }
      console.error("OpenDM rejected:", data);
    } else {
      console.log("OpenDM queued:", data);
    }
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
