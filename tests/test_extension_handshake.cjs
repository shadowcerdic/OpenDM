// Node-based test for spec item 18:
// extension/background.js — cancel-after-ACK ordering.
//
// Loads extension/background.js in a VM sandbox with a stubbed `chrome` API
// and verifies, via logged event ordering, that:
//  1. Daemon returns {ok:true} (after delay)  -> cancel() is invoked ONLY after the ACK.
//  2. Daemon down                             -> suggest() called (browser download proceeds), cancel NOT called.
//  3. Daemon 401 once, then {ok:true}         -> cached token cleared, token refetched via
//                                                native messaging, exactly ONE retry, then cancel.
//  4. Daemon returns non-ok (400)             -> suggest() called, cancel NOT called.
//
// Run: /home/hatch/workspace/opendm/.venv/bin/python is NOT used here (JS test).
//      node tests/test_extension_handshake.cjs

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const BG_PATH = path.join(__dirname, "..", "extension", "background.js");
const src = fs.readFileSync(BG_PATH, "utf8");

function makeWorld() {
  const events = []; // ordered event log
  const store = { opendm_token: "tok-1", opendm_autocatch: true };
  let determineListener = null;
  let fetchQueue = []; // scenario queue: {type:'ok'} | {type:'status',status,data} | {type:'error',message}
  let fetchCalls = 0;
  let nativeCalls = 0;
  let nativeToken = "tok-2"; // what native messaging returns on refetch

  const chrome = {
    runtime: {
      onInstalled: { addListener() {} },
      lastError: null,
      sendNativeMessage(host, msg, cb) {
        nativeCalls++;
        events.push({ t: "native", host, msg });
        if (chrome.runtime.lastError) {
          setImmediate(() => cb(undefined));
        } else {
          setImmediate(() => cb({ ok: true, token: nativeToken }));
        }
      },
    },
    contextMenus: {
      removeAll(cb) { if (cb) cb(); },
      create() {},
      onClicked: { addListener() {} },
    },
    storage: {
      local: {
        get(keys, cb) {
          const o = {};
          for (const k of keys) if (k in store) o[k] = store[k];
          if (cb) setImmediate(() => cb(o));
          return Promise.resolve(o);
        },
        set(o) { Object.assign(store, o); return Promise.resolve(); },
        remove(keys) {
          for (const k of keys) delete store[k];
          return Promise.resolve();
        },
      },
    },
    cookies: { getAll: async () => [] },
    downloads: {
      onDeterminingFilename: { addListener(fn) { determineListener = fn; } },
      cancel(id, cb) { events.push({ t: "cancel", id }); if (cb) cb(); },
      erase(q) { events.push({ t: "erase", id: q.id }); },
    },
    action: {
      setBadgeText(o) { events.push({ t: "badge", text: o.text }); },
      setBadgeBackgroundColor() {},
    },
  };

  const sandbox = {
    chrome,
    navigator: { userAgent: "NodeTest/1.0" },
    console,
    setTimeout, clearTimeout, setImmediate,
    fetch: async (url, opts) => {
      fetchCalls++;
      events.push({ t: "fetch-start", call: fetchCalls, token: (opts.headers || {})["X-OpenDM-Token"] });
      const spec = fetchQueue[fetchCalls - 1] || { type: "error", message: "no stubbed response" };
      if (spec.type === "error") {
        events.push({ t: "fetch-error", call: fetchCalls });
        throw new Error(spec.message);
      }
      if (spec.delayMs) await new Promise(r => setTimeout(r, spec.delayMs));
      const data = spec.data !== undefined ? spec.data : { ok: true, job_id: "j1" };
      events.push({ t: "fetch-resolve", call: fetchCalls, status: spec.status || 200, ok: data && data.ok });
      return {
        status: spec.status || 200,
        ok: (spec.status || 200) >= 200 && (spec.status || 200) < 300,
        json: async () => data,
      };
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: "background.js" });

  const suggestLog = [];
  const suggest = (filename) => {
    events.push({ t: "suggest", filename });
    suggestLog.push(filename);
  };
  const item = { id: 7, url: "https://example.com/file.zip", finalUrl: "https://example.com/file.zip", filename: "downloads/file.zip", mime: "application/zip", referrer: "" };

  async function settle(maxMs = 5000) {
    const start = Date.now();
    while (Date.now() - start < maxMs) {
      const fetchSettled = fetchCalls > 0 &&
        events.filter(e => e.t === "fetch-resolve" || e.t === "fetch-error").length >= fetchCalls;
      const done = events.some(e => e.t === "suggest") ||
        (events.some(e => e.t === "cancel") && events.some(e => e.t === "erase") && fetchSettled);
      if (done) return;
      await new Promise(r => setTimeout(r, 10));
    }
    throw new Error("timeout waiting for handler to settle; events=" + JSON.stringify(events.map(e => e.t)));
  }

  return {
    events, store, suggestLog,
    setFetchQueue(q) { fetchQueue = q; },
    getFetchCalls() { return fetchCalls; },
    getNativeCalls() { return nativeCalls; },
    setNativeToken(t) { nativeToken = t; },
    async run() {
      const ret = determineListener(item, suggest);
      if (ret !== true) throw new Error("handler must return true (async suggest)");
      await settle();
    },
    types() { return events.map(e => e.t); },
    indexOf(t) { return events.findIndex(e => e.t === t); },
    lastIndexOf(t) {
      let i = -1; events.forEach((e, k) => { if (e.t === t) i = k; }); return i;
    },
    count(t) { return events.filter(e => e.t === t).length; },
  };
}

let failures = 0;
async function check(name, fn) {
  try {
    await fn();
    console.log(`PASS: ${name}`);
  } catch (e) {
    failures++;
    console.log(`FAIL: ${name}\n      ${e.message}`);
  }
}

(async () => {
  await check("1. cancel() only AFTER daemon ACK {ok:true}", async () => {
    const w = makeWorld();
    w.setFetchQueue([{ type: "ok", delayMs: 120, data: { ok: true, job_id: "j1" } }]);
    await w.run();
    const iFetchResolve = w.indexOf("fetch-resolve");
    const iCancel = w.indexOf("cancel");
    const iSuggest = w.indexOf("suggest");
    if (iCancel === -1) throw new Error("cancel() never called after ACK");
    if (iFetchResolve === -1) throw new Error("fetch never resolved (stub issue)");
    if (iCancel < iFetchResolve) throw new Error(`cancel() at event ${iCancel} fired BEFORE ACK at event ${iFetchResolve} (order: ${w.types().join(",")})`);
    if (iSuggest !== -1) throw new Error("suggest() should not be called on ACK path");
    if (w.indexOf("erase") === -1) throw new Error("erase() not called");
  });

  await check("2. daemon down -> browser download proceeds (suggest, no cancel)", async () => {
    const w = makeWorld();
    w.setFetchQueue([{ type: "error", message: "ECONNREFUSED" }]);
    await w.run();
    if (w.indexOf("suggest") === -1) throw new Error("suggest() not called on daemon failure");
    if (w.indexOf("cancel") !== -1) throw new Error("cancel() called even though daemon was unreachable");
    const ev = w.events.find(e => e.t === "suggest");
    if (ev.filename !== undefined) throw new Error("suggest() must be called with no modification");
  });

  await check("3. 401 once -> clear token, refetch via native messaging, exactly one retry, then cancel", async () => {
    const w = makeWorld();
    w.setNativeToken("tok-2");
    w.setFetchQueue([
      { type: "status", status: 401, data: { ok: false, error: "unauthorized" } },
      { type: "ok", data: { ok: true, job_id: "j2" } },
    ]);
    await w.run();
    if (w.getFetchCalls() !== 2) throw new Error(`expected exactly 2 fetch calls (1 + 1 retry), got ${w.getFetchCalls()}`);
    if (w.getNativeCalls() !== 1) throw new Error(`expected exactly 1 native-messaging refetch, got ${w.getNativeCalls()}`);
    const secondToken = w.events.find(e => e.t === "fetch-start" && e.call === 2).token;
    if (secondToken !== "tok-2") throw new Error(`retry did not use refetched token (got ${secondToken})`);
    if (w.indexOf("cancel") === -1) throw new Error("cancel() not called after retry ACK");
    if (w.indexOf("suggest") !== -1) throw new Error("suggest() should not be called on retry ACK path");
  });

  await check("4. daemon non-ok (400) -> suggest, no cancel", async () => {
    const w = makeWorld();
    w.setFetchQueue([{ type: "status", status: 400, data: { ok: false, error: "bad request" } }]);
    await w.run();
    if (w.indexOf("suggest") === -1) throw new Error("suggest() not called on non-ok daemon response");
    if (w.indexOf("cancel") !== -1) throw new Error("cancel() called even though daemon rejected the job");
  });

  console.log(failures ? `\n${failures} FAILING` : "\nALL PASS");
  process.exit(failures ? 1 : 0);
})();
