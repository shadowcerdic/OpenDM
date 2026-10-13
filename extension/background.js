// OpenDM Integration v1.1.0 — sends download links to local daemon
// Requires token from options page. Forwards Referer + cookies of the target URL.

const DAEMON_URL = "http://127.0.0.1:10010/api/v1/download";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "opendm-download",
    title: "Download with OpenDM (multi-connection)",
    contexts: ["link", "video", "audio"]
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url = info.linkUrl || info.srcUrl;
  if (!url) return;

  // Retrieve auth token
  const cfg = await chrome.storage.local.get(["opendm_token"]);
  const token = cfg.opendm_token || "";
  if (!token) {
    console.warn("OpenDM: no token configured — open extension options to paste token.");
    chrome.runtime.openOptionsPage();
    return;
  }

  // Gather cookies for the target URL domain (least privilege: only this URL)
  let cookieHeader = "";
  try {
    const cookies = await chrome.cookies.getAll({ url });
    cookieHeader = cookies.map(c => `${c.name}=${c.value}`).join("; ");
  } catch (e) {
    // optional cookies permission might not be granted
  }

  const payload = {
    url,
    connections: 4,
    headers: {
      "User-Agent": navigator.userAgent,
      ...(tab && tab.url ? { "Referer": tab.url } : {}),
      ...(cookieHeader ? { "Cookie": cookieHeader } : {})
    }
  };

  try {
    const res = await fetch(DAEMON_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-OpenDM-Token": token
      },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) {
      console.error("OpenDM daemon rejected task:", data);
    } else {
      console.log("OpenDM queued job:", data);
    }
  } catch (err) {
    console.error("OpenDM daemon unreachable (is it running?):", err);
  }
});
