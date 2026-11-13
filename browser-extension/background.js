// Credentials stay in the extension background context, never in page scripts.
const api = chrome;
const PERIODIC = "stream247-sync";
const CHANGED = "stream247-cookie-change";
let queue = Promise.resolve();
function serial(fn) {
  const next = queue.then(fn, fn);
  queue = next.catch(() => {});
  return next;
}
async function connection() { return (await api.storage.local.get("connection")).connection || null; }
async function save(value) { await api.storage.local.set({ connection: value }); }
function origin(value) {
  const url = new URL(value);
  const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  if ((url.protocol !== "https:" && !(url.protocol === "http:" && local)) ||
      url.username || url.password || url.search || url.hash || url.pathname !== "/") {
    throw new Error("Use an HTTPS server address. HTTP is allowed only on localhost or 127.0.0.1.");
  }
  return url.origin;
}
async function request(server, path, token, body) {
  server = origin(server);
  if (!await api.permissions.contains({ origins: [server + "/*"] })) {
    throw new Error("Server permission is missing. Reconnect and grant access to the selected server.");
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const headers = {};
    if (token) headers.Authorization = "Bearer " + token;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(server + "/api/youtube-browser/" + path, {
      method: body === undefined ? "GET" : "POST", headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "omit", cache: "no-store", redirect: "error", signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      if (data.error === "Browser pairing is no longer valid") {
        const error = new Error("Browser disconnected by the server. Pair again to reconnect.");
        error.revoked = true;
        throw error;
      }
      const allowed = new Set(["Pairing code is invalid or expired", "Automatic sync is disabled",
        "Use HTTPS through a local reverse proxy to connect a browser on another device"]);
      throw new Error(allowed.has(data.error) ? data.error : "Stream247 rejected the request. Check your connection and pairing.");
    }
    return data;
  } catch (error) {
    if (error.revoked || error.message.startsWith("Stream247 ") ||
        error.message.startsWith("Pairing code ") || error.message === "Automatic sync is disabled" ||
        error.message.startsWith("Use HTTPS ")) throw error;
    throw new Error("Could not reach Stream247. Check the server address, HTTPS certificate, and network connection.");
  } finally { clearTimeout(timeout); }
}
async function schedule(autoSync, changed = false) {
  if (autoSync) {
    // Recreate the periodic alarm when a service worker/browser restarts.
    if (!await api.alarms.get(PERIODIC)) await api.alarms.create(PERIODIC, { periodInMinutes: 1 });
    if (changed) await api.alarms.create(CHANGED, { delayInMinutes: 0.5 });
  } else {
    await api.alarms.clear(PERIODIC);
    await api.alarms.clear(CHANGED);
  }
}
function publicState(value) {
  return value ? { connected: true, server: value.server, autoSync: value.autoSync === true,
    lastSync: value.lastSync || null, signedIn: value.signedIn, error: value.error || "" } : { connected: false };
}
async function cookieText() {
  const cookies = (await api.cookies.getAll({ domain: "youtube.com" }))
    .filter(c => !c.partitionKey && (c.domain === "youtube.com" || c.domain.endsWith(".youtube.com")));
  // Presence is a hint, not proof that YouTube will accept this session.
  const signedIn = cookies.some(c => c.value && (c.name.includes("SID") || c.name === "LOGIN_INFO"));
  if (!signedIn) return { text: "", signedIn: false };
  cookies.sort((a, b) => (a.domain + a.path + a.name).localeCompare(b.domain + b.path + b.name));
  const rows = cookies.map(c => [
    (c.httpOnly ? "#HttpOnly_" : "") + c.domain,
    c.hostOnly ? "FALSE" : "TRUE", c.path, c.secure ? "TRUE" : "FALSE",
    Math.max(0, Math.floor(c.expirationDate || 0)), c.name, c.value,
  ].join("\t"));
  return { text: "# Netscape HTTP Cookie File\n" + rows.join("\n") + "\n", signedIn: true };
}
async function sync(manual) {
  const value = await connection();
  if (!value) { if (manual) throw new Error("Pair a browser first."); return; }
  if (!manual && value.autoSync !== true) return;
  const remote = await request(value.server, "client", value.token);
  // The server is authoritative, including revocations and opt-in state.
  value.autoSync = remote.browser.auto_sync === true;
  await schedule(value.autoSync);
  if (!manual && !value.autoSync) { await save(value); return; }
  const cookies = await cookieText();
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(cookies.text));
  const hash = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");
  if (!manual && value.lastHash === hash) {
    value.error = "";
    await save(value);
    return;
  }
  const result = await request(value.server, "sync", value.token, {
    cookies: cookies.text, mode: manual ? "manual" : "automatic",
  });
  value.lastHash = hash;
  value.lastSync = result.browser.last_sync;
  value.signedIn = cookies.signedIn;
  value.error = "";
  await save(value);
}
async function rememberError(error) {
  if (error.revoked) {
    await save(null);
    await schedule(false);
  } else {
    const value = await connection();
    if (value) { value.error = error.message; await save(value); }
  }
}
async function dispatch(message) {
  if (message.type === "pair") {
    if (await connection()) throw new Error("Disconnect the current server before pairing another.");
    const server = origin(message.server);
    const result = await request(server, "pair", null, { code: message.code, device: message.device,
      auto_sync: message.autoSync === true });
    if (typeof result.token !== "string" || !/^[a-f0-9]{64}$/.test(result.token)) throw new Error("Invalid pairing response from Stream247.");
    await save({ server, token: result.token, autoSync: result.browser.auto_sync === true, error: "" });
    await schedule(result.browser.auto_sync === true);
    await sync(true);
  } else if (message.type === "sync") {
    await sync(true);
  } else if (message.type === "configure") {
    const value = await connection();
    if (!value) throw new Error("Pair a browser first.");
    const result = await request(value.server, "config", value.token, { auto_sync: message.autoSync === true });
    value.autoSync = result.browser.auto_sync === true;
    value.error = "";
    await save(value);
    await schedule(value.autoSync, value.autoSync);
  } else if (message.type === "disconnect") {
    const value = await connection();
    if (value) {
      // Keep local credentials if unreachable so revocation can be retried.
      await request(value.server, "client-disconnect", value.token, {});
      await save(null);
      await schedule(false);
    }
  } else if (message.type === "status") {
    const value = await connection();
    if (value) {
      const result = await request(value.server, "client", value.token);
      value.autoSync = result.browser.auto_sync === true;
      value.lastSync = result.browser.last_sync;
      value.error = "";
      await save(value);
      await schedule(value.autoSync);
    }
  } else { throw new Error("Unknown extension action."); }
  return { ok: true, state: publicState(await connection()) };
}
api.action.onClicked.addListener(() => {
  api.tabs.create({ url: api.runtime.getURL("connect.html") });
});
api.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== api.runtime.id || sender.url !== api.runtime.getURL("connect.html")) return false;
  serial(async () => {
    try { sendResponse(await dispatch(message)); }
    catch (error) {
      await rememberError(error);
      sendResponse({ ok: false, error: error.message, state: publicState(await connection()) });
    }
  });
  return true;
});
api.cookies.onChanged.addListener(change => {
  const domain = change.cookie.domain;
  if (domain !== "youtube.com" && !domain.endsWith(".youtube.com")) return;
  serial(async () => {
    const value = await connection();
    if (value?.autoSync === true) await schedule(true, true);
  }).catch(() => {});
});
api.alarms.onAlarm.addListener(alarm => {
  if (alarm.name !== PERIODIC && alarm.name !== CHANGED) return;
  serial(async () => { try { await sync(false); } catch (error) { await rememberError(error); } }).catch(() => {});
});
async function initialize() {
  if (api.storage.local.setAccessLevel) await api.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
  const value = await connection();
  await schedule(value?.autoSync === true);
}
// Run whenever the service worker starts, including browser restarts.
serial(initialize).catch(() => {});
