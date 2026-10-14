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
 * Pass forceRefresh=true to bypass the cache (e.g. after a 401) and re-fetch
 * the token via native messaging.
 */
async function getToken(forceRefresh = false) {
  if (!forceRefresh) {
    const cfg = await chrome.storage.local.get(["opendm_token"]);
    if (cfg.opendm_token) {
      return cfg.opendm_token;
    }
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

/**
 * Sends a download job to the OpenDM daemon.
 * Returns true ONLY if the daemon positively accepted the job ({ok:true} on 2xx).
 * Returns false on daemon unreachable, non-ok response, or exception.
 * On 401: clears the cached token, re-fetches it via native messaging, and
 * retries the request exactly once before giving up.
 */
async function sendToDaemon(url, headers, suggestedFilename) {
  const payload = {
    url,
    connections: 4,
    headers,
    show_dialog: true,
    ...(suggestedFilename ? { suggested_filename: suggestedFilename } : {})
  };

  for (let attempt = 1; attempt <= 2; attempt++) {
    const token = await getToken(/* forceRefresh */ attempt > 1);
    try {
      const res = await fetch(DEFAULT_DAEMON_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { "X-OpenDM-Token": token } : {})
        },
        body: JSON.stringify(payload)
      });

      let data = null;
      try {
        data = await res.json();
      } catch (e) {
        data = null;
      }

      if (res.status === 401) {
        // Token invalid or rotated: drop the cache and retry exactly once
        // with a freshly-fetched token.
        await chrome.storage.local.remove(["opendm_token"]);
        if (attempt === 1) {
          console.warn("OpenDM: 401 from daemon, refreshing token and retrying once");
          continue;
        }
        console.error("OpenDM rejected (401 after retry):", data);
        return false;
      }

      if (res.ok && data && data.ok === true) {
        console.log("OpenDM queued:", data);
        return true;
      }
      console.error("OpenDM rejected:", res.status, data);
      return false;
    } catch (err) {
      console.error("OpenDM daemon unreachable:", err);
      return false;
    }
  }
  return false;
}

/** Surface failures to the user: badge (no new permission needed) + console. */
function showErrorBadge() {
  try {
    chrome.action.setBadgeText({ text: "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#d73a49" });
    setTimeout(clearErrorBadge, 10000);
  } catch (e) {}
}
function clearErrorBadge() {
  try {
    chrome.action.setBadgeText({ text: "" });
  } catch (e) {}
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

// Auto-catch: send the download to OpenDM, and cancel Chrome's native
// download ONLY after the daemon confirms it accepted the job ({ok:true}).
// If the daemon is unreachable or rejects the job, the browser download
// proceeds normally so the user's download is never lost.
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

    const filename = item.filename ? item.filename.split(/[\\/]/).pop() : null;
    const headers = await collectHeaders(url, item.referrer || "");

    let accepted = false;
    try {
      accepted = await sendToDaemon(url, headers, filename);
    } catch (err) {
      console.error("OpenDM send failed:", err);
      accepted = false;
    }

    if (accepted) {
      // Daemon confirmed it took the job — now cancel the native download.
      chrome.downloads.cancel(item.id, () => {
        chrome.downloads.erase({ id: item.id });
      });
      clearErrorBadge();
    } else {
      // Daemon did not take the job — let the browser download normally.
      showErrorBadge();
      suggest();
    }
  });
  return true;
});
