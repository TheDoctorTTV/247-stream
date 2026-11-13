const api = chrome;
const byId = id => document.getElementById(id);
let busy = false;
function serverOrigin(value) {
  const url = new URL(value);
  const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  if ((url.protocol !== "https:" && !(url.protocol === "http:" && local)) ||
      url.username || url.password || url.search || url.hash || url.pathname !== "/") {
    throw new Error("Use an HTTPS server address. HTTP is allowed only on localhost or 127.0.0.1.");
  }
  return url.origin;
}
function showStatus(message, error = false) {
  byId("status").textContent = message;
  byId("status").className = error ? "error" : "";
}
function render(state) {
  byId("pairForm").hidden = state.connected;
  byId("connected").hidden = !state.connected;
  if (state.connected) {
    byId("destination").textContent = "Connected to " + state.server;
    byId("autoSync").checked = state.autoSync === true;
    byId("lastSync").textContent = state.lastSync ? "Last sync: " + new Date(state.lastSync * 1000).toLocaleString() : "Not synced yet";
  } else {
    byId("pairAuto").checked = false;
  }
  if (state.error) showStatus(state.error, true);
}
async function command(type, fields = {}) {
  const result = await api.runtime.sendMessage({ type, ...fields });
  if (result?.state) render(result.state);
  if (!result || !result.ok) throw new Error(result?.error || "Could not contact the extension.");
  return result;
}
async function action(fn) {
  if (busy) return;
  busy = true;
  for (const control of document.querySelectorAll("button, input")) control.disabled = true;
  try { await fn(); }
  catch (error) { showStatus(error.message, true); }
  finally {
    busy = false;
    for (const control of document.querySelectorAll("button, input")) control.disabled = false;
  }
}
byId("pairForm").addEventListener("submit", event => {
  event.preventDefault();
  // Request the chosen destination directly from the user's click, before any await.
  let server;
  try { server = serverOrigin(byId("server").value.trim()); }
  catch (error) { showStatus(error.message, true); return; }
  const permission = api.permissions.request({ origins: [server + "/*"] });
  action(async () => {
    if (!await permission) throw new Error("Server permission was not granted.");
    showStatus("Pairing…");
    const result = await command("pair", { server, code: byId("code").value.trim(), device: byId("device").value, autoSync: byId("pairAuto").checked });
    byId("code").value = "";
    showStatus(result.state.signedIn === false ? "Paired. Sign in to YouTube, then sync again." : "Browser paired and synced.");
  });
});
byId("sync").addEventListener("click", () => action(async () => {
  showStatus("Syncing…");
  const result = await command("sync");
  showStatus(result.state.signedIn === false ? "No signed-in YouTube session found. Saved server cookies cleared." : "Sync complete.");
}));
byId("autoSync").addEventListener("change", () => action(async () => {
  const enabled = byId("autoSync").checked;
  try {
    await command("configure", { autoSync: enabled });
    showStatus(enabled ? "Automatic sync enabled." : "Automatic sync disabled.");
  } catch (error) {
    byId("autoSync").checked = !enabled;
    throw error;
  }
}));
byId("disconnect").addEventListener("click", () => action(async () => {
  await command("disconnect");
  showStatus("Disconnected. Saved server cookies removed.");
}));
action(() => command("status"));
