    const stateText = document.getElementById("stateText");
    const metaText = document.getElementById("metaText");
    const otherLogBox = document.getElementById("otherLogBox");
    const ffmpegLogBox = document.getElementById("ffmpegLogBox");
    const startBtn = document.getElementById("startBtn");
    const stopBtn = document.getElementById("stopBtn");
    const skipBtn = document.getElementById("skipBtn");
    const refreshBtn = document.getElementById("refreshBtn");
    const reloadSettingsBtn = document.getElementById("reloadSettingsBtn");
    const settingsStatus = document.getElementById("settingsStatus");
    const checkAppUpdateBtn = document.getElementById("checkAppUpdateBtn");
    const downloadAppUpdateBtn = document.getElementById("downloadAppUpdateBtn");
    const reinstallAppBtn = document.getElementById("reinstallAppBtn");
    const appUpdateStatus = document.getElementById("appUpdateStatus");
    const appUpdateProgressBar = document.getElementById("appUpdateProgressBar");
    const appUpdateProgressText = document.getElementById("appUpdateProgressText");
    const appUpdateVersion = document.getElementById("app_update_version");
    const updateBinariesBtn = document.getElementById("updateBinariesBtn");
    const binariesStatus = document.getElementById("binariesStatus");
    const binariesProgressBar = document.getElementById("binariesProgressBar");
    const binariesProgressText = document.getElementById("binariesProgressText");
    const sourceAddName = document.getElementById("source_add_name");
    const sourceAddInput = document.getElementById("source_add_input");
    const sourceAddBtn = document.getElementById("source_add_btn");
    const sourcesList = document.getElementById("sources_list");
    const sourcesEmpty = document.getElementById("sources_empty");
    const sourceHint = document.getElementById("sourceHint");
    const sourceSelect = document.getElementById("playlist_url");
    const streamUrlPreset = document.getElementById("stream_url_preset");
    const rtmpBaseField = document.getElementById("rtmp_base_field");
    const rtmpBaseInput = document.getElementById("rtmp_base");
    const themeSelect = document.getElementById("theme_select");
    const tabButtons = Array.from(document.querySelectorAll(".tab-btn"));
    const tabPanels = Array.from(document.querySelectorAll(".tab-panel"));
    const subtabButtons = Array.from(document.querySelectorAll(".subtab-btn"));
    const subtabPanels = Array.from(document.querySelectorAll(".subtab-panel"));
    let busy = false;
    let appUpdateRunning = false;
    let binariesUpdateRunning = false;
    let prevAppUpdateRunning = false;
    let prevBinariesUpdateRunning = false;
    let appJustUpdatedUntil = 0;
    let binariesJustUpdatedUntil = 0;
    let lastAppCheckAt = "";
    let lastBinariesCheckAt = "";
    let suppressAutoSave = false;
    let autoSaveTimer = null;
    let saveInFlight = false;
    let pendingAutoSave = false;
    let lastSavedPayload = "";
    let appVersionsBoundToChannel = "";
    const APP_UPDATE_AUTO_CHECK_MS = 10 * 60 * 1000;
    let appAutoCheckTimer = null;
    let appUpdateRefreshScheduled = false;
    let appUpdateLoadFailures = 0;
    const STORAGE_TAB_KEY = "stream247.active_tab";
    const STORAGE_SUBTAB_KEY = "stream247.active_subtab";
    const fieldIds = ["playlist_url", "rtmp_base", "stream_key", "resolution", "framerate", "video_bitrate", "buffer_mode", "encoder_preference", "yt_auth_enabled", "yt_auth_browser", "yt_auth_profile", "overlay_titles", "shuffle", "log_to_file", "ffmpeg_log_to_file", "remember", "check_updates_startup", "auto_app_updates", "app_update_channel"];
    const STREAM_URL_PRESETS = {
      youtube: "rtmp://a.rtmp.youtube.com/live2",
      twitch: "rtmp://live.twitch.tv/app",
      kick: "rtmps://fa723fc1b171.global-contribute.live-video.net:443/app",
      facebook: "rtmps://live-api-s.facebook.com:443/rtmp/",
      tiktok: "rtmps://push-rtmp-global.tiktok.com/live/",
      trovo: "rtmp://livepush.trovo.live/live/"
    };
    let lastCustomStreamUrl = "";

    let sourcesState = [];

    function normalizeSources(value) {
      const out = [];
      const seen = new Set();
      const push = (raw) => {
        const src = String(raw || "").trim();
        if (!src || seen.has(src)) return;
        seen.add(src);
        out.push(src);
      };
      if (Array.isArray(value)) {
        value.forEach(push);
      } else if (typeof value === "string") {
        value.split(/\r?\n/g).forEach(push);
      }
      return out;
    }

    function normalizeSourceNames(value) {
      const out = {};
      if (!value || typeof value !== "object" || Array.isArray(value)) return out;
      for (const [rawUrl, rawName] of Object.entries(value)) {
        const url = String(rawUrl || "").trim();
        const name = String(rawName || "").trim();
        if (!url || !name) continue;
        out[url] = name;
      }
      return out;
    }

    function normalizeSourceEntries(value, sourceNames) {
      const urls = normalizeSources(value);
      const namesMap = normalizeSourceNames(sourceNames);
      if (Array.isArray(value)) {
        value.forEach((item) => {
          if (!item || typeof item !== "object" || Array.isArray(item)) return;
          const url = String(item.url || item.source || item.playlist_url || "").trim();
          const name = String(item.name || "").trim();
          if (url && name && !namesMap[url]) namesMap[url] = name;
        });
      }
      return urls.map((url) => ({ url, name: namesMap[url] || "" }));
    }

    function truncateUrl(url) {
      const src = String(url || "").trim();
      const limit = (window.innerWidth <= 700) ? 36 : 70;
      if (src.length <= limit) return src;
      return src.slice(0, Math.max(8, limit - 3)) + "...";
    }

    function sourceLabel(entry) {
      const url = String(entry && entry.url || "").trim();
      const name = String(entry && entry.name || "").trim();
      if (!url) return "";
      if (name) return name;
      return truncateUrl(url);
    }

    function sourceNameForUrl(url) {
      const wanted = String(url || "").trim();
      if (!wanted) return "";
      const match = sourcesState.find((entry) => String(entry && entry.url || "").trim() === wanted);
      return String(match && match.name || "").trim();
    }

    function normalizeStateEntries(value) {
      const raw = Array.isArray(value) ? value : [];
      const byUrl = new Map();
      raw.forEach((entry) => {
        const url = String(entry && entry.url || "").trim();
        if (!url) return;
        const name = String(entry && entry.name || "").trim();
        if (!byUrl.has(url)) {
          byUrl.set(url, { url, name });
        } else if (name && !String(byUrl.get(url).name || "").trim()) {
          byUrl.get(url).name = name;
        }
      });
      return Array.from(byUrl.values());
    }

    async function copyTextToClipboard(text) {
      const value = String(text || "");
      if (!value) return false;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(value);
          return true;
        }
      } catch (err) {
      }
      try {
        const ta = document.createElement("textarea");
        ta.value = value;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        ta.style.top = "0";
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        const ok = document.execCommand("copy");
        document.body.removeChild(ta);
        return !!ok;
      } catch (err) {
        return false;
      }
    }

    function renderSourcesList() {
      sourcesList.innerHTML = "";
      if (!sourcesState.length) {
        sourcesEmpty.style.display = "";
        return;
      }
      sourcesEmpty.style.display = "none";
      sourcesState.forEach((entry, idx) => {
        const row = document.createElement("div");
        row.className = "source-item";

        const main = document.createElement("div");
        main.className = "source-main";

        const name = document.createElement("div");
        name.className = "source-name";
        name.textContent = String(entry.name || "").trim() || "Unnamed Source";

        const url = document.createElement("div");
        url.className = "source-url";
        url.textContent = truncateUrl(entry.url);
        url.title = entry.url;

        const copyBtn = document.createElement("button");
        copyBtn.className = "source-copy";
        copyBtn.type = "button";
        copyBtn.textContent = "Copy URL";
        copyBtn.addEventListener("click", async () => {
          const ok = await copyTextToClipboard(entry.url);
          if (ok) {
            setSettingsStatus("Copied source URL to clipboard.", "ok");
          } else {
            setSettingsStatus("Failed to copy source URL.", "err");
          }
        });

        const removeBtn = document.createElement("button");
        removeBtn.className = "source-remove";
        removeBtn.type = "button";
        removeBtn.textContent = "Remove";
        removeBtn.addEventListener("click", () => {
          const nextSources = sourcesState.filter((_, i) => i !== idx);
          const selected = String(sourceSelect.value || "").trim();
          const preferred = (selected && selected !== entry.url) ? selected : "";
          setSourcesState(nextSources, preferred);
          queueAutoSave(180);
        });

        main.appendChild(name);
        main.appendChild(url);
        row.appendChild(main);
        row.appendChild(copyBtn);
        row.appendChild(removeBtn);
        sourcesList.appendChild(row);
      });
    }

    function syncSourceSelect(entries, preferred) {
      const safeEntries = normalizeStateEntries(entries);
      sourceSelect.innerHTML = "";
      if (!safeEntries.length) {
        const emptyOption = document.createElement("option");
        emptyOption.value = "";
        emptyOption.textContent = "No sources configured";
        sourceSelect.appendChild(emptyOption);
        sourceSelect.value = "";
        sourceSelect.disabled = true;
        sourceHint.textContent = "Add source URLs in the Sources tab.";
        return "";
      }
      safeEntries.forEach((entry) => {
        const opt = document.createElement("option");
        opt.value = entry.url;
        opt.textContent = sourceLabel(entry);
        opt.title = entry.url;
        sourceSelect.appendChild(opt);
      });
      const urls = safeEntries.map((entry) => entry.url);
      const wanted = urls.includes(preferred) ? preferred : urls[0];
      sourceSelect.value = wanted;
      sourceSelect.disabled = (safeEntries.length <= 1);
      sourceHint.textContent = (safeEntries.length <= 1)
        ? "Only one source is configured. It will be used automatically."
        : "Choose which source to stream.";
      return wanted;
    }

    function setSourcesState(nextSources, preferred) {
      sourcesState = normalizeStateEntries(nextSources);
      const selected = syncSourceSelect(sourcesState, preferred);
      renderSourcesList();
      return selected;
    }

    function addSourceFromInput() {
      const rawUrl = String(sourceAddInput.value || "").trim();
      const rawName = String(sourceAddName && sourceAddName.value || "").trim();
      if (!rawUrl) return;
      const selected = String(sourceSelect.value || "").trim();
      const nextSources = [{ url: rawUrl, name: rawName }];
      sourcesState.forEach((entry) => {
        if (entry.url === rawUrl) return;
        nextSources.push({ url: entry.url, name: entry.name });
      });
      const preferred = selected && selected !== rawUrl ? selected : rawUrl;
      setSourcesState(nextSources, preferred);
      sourceAddInput.value = "";
      if (sourceAddName) sourceAddName.value = "";
      queueAutoSave(180);
    }

    function syncSourcesUIFromSettings(settings) {
      const sources = normalizeSourceEntries(settings.sources, settings.source_names);
      const fallbackPlaylist = String(settings.playlist_url || "").trim();
      if (!sources.length && fallbackPlaylist) {
        sources.push({ url: fallbackPlaylist, name: "" });
      }
      return setSourcesState(sources, fallbackPlaylist);
    }

    function normalizeStreamUrl(url) {
      return String(url || "").trim().replace(/\/+$/, "");
    }

    function streamPresetFromUrl(url) {
      const wanted = normalizeStreamUrl(url);
      if (!wanted) return "youtube";
      for (const [presetKey, presetUrl] of Object.entries(STREAM_URL_PRESETS)) {
        if (normalizeStreamUrl(presetUrl) === wanted) return presetKey;
      }
      return "custom";
    }

    function setStreamUrlUiFromCurrentValue() {
      if (!streamUrlPreset || !rtmpBaseInput) return;
      const selectedPreset = String(streamUrlPreset.value || "youtube");
      const isCustom = (selectedPreset === "custom");
      if (rtmpBaseField) {
        rtmpBaseField.style.display = isCustom ? "" : "none";
      }
      if (isCustom) {
        if (lastCustomStreamUrl) rtmpBaseInput.value = lastCustomStreamUrl;
      } else {
        const presetUrl = STREAM_URL_PRESETS[selectedPreset] || STREAM_URL_PRESETS.youtube;
        rtmpBaseInput.value = presetUrl;
      }
    }

    function syncStreamPresetFromInputValue() {
      if (!streamUrlPreset || !rtmpBaseInput) return;
      const currentUrl = String(rtmpBaseInput.value || "").trim();
      const preset = streamPresetFromUrl(currentUrl);
      if (preset === "custom") lastCustomStreamUrl = currentUrl;
      streamUrlPreset.value = preset;
      setStreamUrlUiFromCurrentValue();
    }

    function setSettingsStatus(text, level) {
      settingsStatus.textContent = text || "";
      settingsStatus.className = "statusline" + (level ? (" " + level) : "");
    }

    function escapeHtml(text) {
      return String(text || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function setStatusWithDot(el, text, state) {
      const valid = new Set(["updated", "outdated", "just-updated", "downgrading", "running", "error", "unknown"]);
      const cls = valid.has(String(state || "")) ? String(state) : "unknown";
      const msg = escapeHtml(text || "");
      el.className = "statusline status-indicator";
      el.innerHTML = '<span class="status-dot ' + cls + '" aria-hidden="true"></span><span class="status-msg">' + msg + "</span>";
    }

    function setBinariesStatus(text, level) {
      setStatusWithDot(binariesStatus, text, level);
    }

    function setAppUpdateStatus(text, level) {
      setStatusWithDot(appUpdateStatus, text, level);
    }

    function setAppUpdateProgress(percent, text) {
      const p = Math.max(0, Math.min(100, Number(percent) || 0));
      const visual = p > 0 ? Math.max(p, 1.2) : 0;
      appUpdateProgressBar.style.width = visual + "%";
      appUpdateProgressText.textContent = text || (p > 0 ? ("Progress: " + p + "%") : "");
    }

    function setBinariesProgress(percent, text) {
      const p = Math.max(0, Math.min(100, Number(percent) || 0));
      const visual = p > 0 ? Math.max(p, 1.2) : 0;
      binariesProgressBar.style.width = visual + "%";
      binariesProgressText.textContent = text || (p > 0 ? ("Progress: " + p + "%") : "");
    }

    function isAutoUpdateCheckEnabled() {
      const el = document.getElementById("check_updates_startup");
      return !!(el && el.checked);
    }

    function resetAppAutoCheckTimer(runNow) {
      if (appAutoCheckTimer) {
        clearInterval(appAutoCheckTimer);
        appAutoCheckTimer = null;
      }
      if (!isAutoUpdateCheckEnabled()) {
        if (!appUpdateRunning) {
          setAppUpdateStatus("Automatic update checks are disabled. Use Check to run manually.", "unknown");
        }
        return;
      }
      const runCheck = () => {
        if (appUpdateRunning) return;
        triggerAppUpdateCheck("");
      };
      if (runNow) runCheck();
      appAutoCheckTimer = setInterval(runCheck, APP_UPDATE_AUTO_CHECK_MS);
    }

    function normalizeSuffix(suffix) {
      const parts = String(suffix || "").toLowerCase().match(/[a-z]+|\d+/g) || [];
      if (!parts.length) return [];
      const out = [];
      for (let i = 0; i < parts.length; i += 1) {
        const cur = parts[i];
        const next = parts[i + 1] || "";
        if (cur === "pre" && next === "release") {
          out.push("prerelease");
          i += 1;
          continue;
        }
        out.push(cur);
      }
      return out;
    }

    function parseVersion(v) {
      const text = String(v || "").trim().replace(/^v/i, "");
      const m = text.match(/^(\d+(?:\.\d+)*)(.*)$/);
      if (!m) return null;
      const core = m[1].split(".").map((x) => Number(x));
      let suffix = String(m[2] || "").trim();
      if (suffix.startsWith("-")) suffix = suffix.slice(1).trim();
      return { core, suffix: normalizeSuffix(suffix) };
    }

    function compareVersions(current, latest) {
      const a = parseVersion(current);
      const b = parseVersion(latest);
      if (!a || !b) return null;
      const n = Math.max(a.core.length, b.core.length);
      for (let i = 0; i < n; i++) {
        const av = a.core[i] || 0;
        const bv = b.core[i] || 0;
        if (av > bv) return 1;
        if (av < bv) return -1;
      }
      if (!a.suffix.length && b.suffix.length) return 1;
      if (a.suffix.length && !b.suffix.length) return -1;
      const n2 = Math.max(a.suffix.length, b.suffix.length);
      for (let i = 0; i < n2; i++) {
        if (i >= a.suffix.length) return -1;
        if (i >= b.suffix.length) return 1;
        const avRaw = a.suffix[i];
        const bvRaw = b.suffix[i];
        const avNum = /^\d+$/.test(avRaw) ? Number(avRaw) : null;
        const bvNum = /^\d+$/.test(bvRaw) ? Number(bvRaw) : null;
        if (avNum !== null && bvNum !== null) {
          if (avNum > bvNum) return 1;
          if (avNum < bvNum) return -1;
          continue;
        }
        if (avNum !== null && bvNum === null) return -1;
        if (avNum === null && bvNum !== null) return 1;
        if (avRaw > bvRaw) return 1;
        if (avRaw < bvRaw) return -1;
      }
      return 0;
    }

    async function api(path) {
      await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await refreshState(true);
    }

    function updateLogBox(el, arr, forceScroll) {
      const logs = Array.isArray(arr) ? arr : [];
      const text = logs.join("\n");
      const wasAtBottom = (el.scrollTop + el.clientHeight + 20 >= el.scrollHeight);
      el.textContent = text || "No logs yet.";
      if (forceScroll || wasAtBottom) {
        el.scrollTop = el.scrollHeight;
      }
    }

    async function refreshState(forceScroll) {
      if (busy) return;
      busy = true;
      try {
        const res = await fetch("/api/state?ts=" + Date.now(), { cache: "no-store" });
        if (!res.ok) throw new Error("state fetch failed");
        const s = await res.json();
        const streaming = !!s.streaming;
        stateText.textContent = s.status || "Unknown";
        stateText.className = "status" + (streaming ? " on" : "");
        const meta = s.meta || {};
        const parts = [];
        const runtimeSourceUrl = String(meta.source || "").trim();
        const selectedSourceUrl = String(sourceSelect && sourceSelect.value || "").trim();
        const firstSourceUrl = String((sourcesState[0] && sourcesState[0].url) || "").trim();
        const sourceUrl = runtimeSourceUrl || selectedSourceUrl || firstSourceUrl;
        if (sourceUrl) {
          const sourceName = sourceNameForUrl(sourceUrl);
          parts.push("source: " + (sourceName || sourceUrl));
        }
        metaText.textContent = parts.join(" | ");
        updateLogBox(otherLogBox, s.logs_other, forceScroll);
        updateLogBox(ffmpegLogBox, s.logs_ffmpeg, forceScroll);
        startBtn.disabled = streaming;
        stopBtn.disabled = !streaming;
        skipBtn.disabled = !streaming;
      } catch (err) {
        stateText.textContent = "Dashboard disconnected";
        stateText.className = "status";
      } finally {
        busy = false;
      }
    }

    function applySettingsToForm(s) {
      const selectedSource = syncSourcesUIFromSettings(s || {});
      for (const id of fieldIds) {
        const el = document.getElementById(id);
        if (!el || !(id in s)) continue;
        if (el.type === "checkbox") {
          el.checked = !!s[id];
        } else if (id === "yt_auth_enabled") {
          el.value = s[id] ? "true" : "false";
        } else if (id === "playlist_url") {
          el.value = selectedSource || String(s[id] ?? "");
        } else {
          el.value = String(s[id] ?? "");
        }
      }
      if (s && Object.prototype.hasOwnProperty.call(s, "theme")) {
        applyTheme(String(s.theme || "blue"));
      }
      syncStreamPresetFromInputValue();
    }

    function formToPayload() {
      const out = {};
      const currentSources = sourcesState.map((entry) => String(entry.url || "").trim()).filter(Boolean);
      const sourceNames = {};
      sourcesState.forEach((entry) => {
        const url = String(entry.url || "").trim();
        const name = String(entry.name || "").trim();
        if (url && name) sourceNames[url] = name;
      });
      const selected = String(sourceSelect.value || "").trim();
      const normalizedSelected = syncSourceSelect(sourcesState, selected);
      for (const id of fieldIds) {
        const el = document.getElementById(id);
        if (!el) continue;
        out[id] = (el.type === "checkbox") ? !!el.checked : el.value;
      }
      if (streamUrlPreset) {
        const selectedPreset = String(streamUrlPreset.value || "youtube");
        if (selectedPreset !== "custom") {
          out.rtmp_base = STREAM_URL_PRESETS[selectedPreset] || STREAM_URL_PRESETS.youtube;
        } else {
          out.rtmp_base = String(out.rtmp_base || "").trim();
        }
      }
      out.theme = String(themeSelect && themeSelect.value || "blue").trim().toLowerCase();
      out.sources = currentSources;
      out.source_names = sourceNames;
      if (currentSources.length === 1) {
        out.playlist_url = currentSources[0];
      } else if (!currentSources.length) {
        out.playlist_url = "";
      } else if (!currentSources.includes(String(out.playlist_url || ""))) {
        out.playlist_url = normalizedSelected || currentSources[0];
      }
      out.framerate = Number(out.framerate || 30);
      out.update_download_cap_mbps = 50;
      out.yt_auth_enabled = (String(out.yt_auth_enabled).toLowerCase() === "true");
      return out;
    }

    function payloadSignature(payload) {
      try {
        return JSON.stringify(payload || {});
      } catch (err) {
        return "";
      }
    }

    function queueAutoSave(delayMs) {
      if (suppressAutoSave) return;
      const delay = Number(delayMs || 550);
      if (autoSaveTimer) clearTimeout(autoSaveTimer);
      setSettingsStatus("Saving changes soon...", "warn");
      autoSaveTimer = setTimeout(() => {
        autoSaveTimer = null;
        saveSettings("auto");
      }, delay);
    }

    function bindAutoSaveListeners() {
      for (const id of fieldIds) {
        const el = document.getElementById(id);
        if (!el) continue;
        if (el.type === "checkbox" || el.tagName === "SELECT") {
          el.addEventListener("change", () => queueAutoSave(180));
        } else {
          el.addEventListener("input", () => queueAutoSave(650));
          el.addEventListener("change", () => queueAutoSave(180));
        }
      }
      if (streamUrlPreset) {
        streamUrlPreset.addEventListener("change", () => {
          setStreamUrlUiFromCurrentValue();
          queueAutoSave(180);
        });
      }
      if (rtmpBaseInput) {
        rtmpBaseInput.addEventListener("input", () => {
          if (!streamUrlPreset || String(streamUrlPreset.value || "") !== "custom") return;
          lastCustomStreamUrl = String(rtmpBaseInput.value || "").trim();
        });
      }
    }

    function applyTheme(themeName) {
      let theme = String(themeName || "blue").trim().toLowerCase();
      if (theme === "current") theme = "blue";
      const allowed = new Set(["blue", "purple", "red", "mono"]);
      const safeTheme = allowed.has(theme) ? theme : "blue";
      const root = document.documentElement;
      if (safeTheme === "blue") {
        root.removeAttribute("data-theme");
        document.body.removeAttribute("data-theme");
      } else {
        root.setAttribute("data-theme", safeTheme);
        document.body.setAttribute("data-theme", safeTheme);
      }
      if (themeSelect) themeSelect.value = safeTheme;
    }

    function initThemeSelector() {
      if (!themeSelect) return;
      applyTheme(String(themeSelect.value || "blue"));
      themeSelect.addEventListener("change", () => {
        const selected = String(themeSelect.value || "blue").trim().toLowerCase();
        applyTheme(selected);
        queueAutoSave(180);
      });
    }

    async function loadSettings() {
      setSettingsStatus("Loading settings...", "");
      try {
        const res = await fetch("/api/settings?ts=" + Date.now(), { cache: "no-store" });
        if (!res.ok) throw new Error("load settings failed");
        const payload = await res.json();
        if (!payload.ok || !payload.settings) throw new Error(payload.error || "invalid response");
        suppressAutoSave = true;
        applySettingsToForm(payload.settings);
        lastSavedPayload = payloadSignature(formToPayload());
        setSettingsStatus("Settings loaded.", "ok");
        resetAppAutoCheckTimer(true);
      } catch (err) {
        setSettingsStatus("Failed to load settings.", "err");
        resetAppAutoCheckTimer(false);
      } finally {
        suppressAutoSave = false;
      }
    }

    async function saveSettings(mode) {
      if (saveInFlight) {
        pendingAutoSave = true;
        return;
      }
      const reason = String(mode || "manual");
      const currentPayload = formToPayload();
      const currentSig = payloadSignature(currentPayload);
      if (reason === "auto" && currentSig && currentSig === lastSavedPayload) {
        setSettingsStatus("Settings saved.", "ok");
        return;
      }
      saveInFlight = true;
      setSettingsStatus(reason === "auto" ? "Saving changes..." : "Saving settings...", "");
      try {
        const res = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(currentPayload) });
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "save failed");
        suppressAutoSave = true;
        applySettingsToForm(payload.settings || {});
        lastSavedPayload = payloadSignature(formToPayload());
        setSettingsStatus(reason === "auto" ? "Changes saved automatically." : "Settings saved.", "ok");
      } catch (err) {
        setSettingsStatus("Failed to save settings.", "err");
      } finally {
        suppressAutoSave = false;
        saveInFlight = false;
        if (pendingAutoSave) {
          pendingAutoSave = false;
          queueAutoSave(220);
        }
      }
    }

    function formatAppUpdateSummary(a) {
      if (!a) return "No app update status available.";
      if (a.running) return a.progress_message || "App update install is running...";
      if (a.last_error) return "App update error: " + a.last_error;
      const r = a.last_result || null;
      if (!r) return "App update status not available yet.";
      const current = String(r.current_version || "unknown");
      const latest = String(r.latest_version || "unknown");
      const selected = String(r.selected_version || latest || "unknown");
      const rel = compareVersions(current, latest);
      const shouldInstall = (!!r.should_install || !!r.is_newer || rel === -1 || rel === 1);
      const assetSupported = (r.asset_supported !== false);
      const selectedCmp = compareVersions(current, selected);
      const downgrading = (selectedCmp === 1);
      let state = "Up to date";
      if (downgrading) state = "Downgrade selected";
      else if (shouldInstall && assetSupported) state = "Install available";
      else if (shouldInstall && !assetSupported) state = "Install available (unsupported asset)";
      let text = state + " | current " + current + " | selected " + selected;
      if (lastAppCheckAt) text += " | checked " + lastAppCheckAt;
      return text;
    }

    function updateAppVersionDropdown(info) {
      if (!info || !info.last_result) return;
      const result = info.last_result;
      const channel = String(result.channel || document.getElementById("app_update_channel").value || "release");
      const versions = Array.isArray(result.available_versions) ? result.available_versions : [];
      const selected = String(result.selected_version || appUpdateVersion.value || "");
      const previous = String(appUpdateVersion.value || "");
      appUpdateVersion.innerHTML = "";
      const fallback = document.createElement("option");
      fallback.value = "";
      fallback.textContent = "Latest in selected channel";
      appUpdateVersion.appendChild(fallback);
      for (const version of versions) {
        const v = String(version || "").trim();
        if (!v) continue;
        const opt = document.createElement("option");
        opt.value = v;
        opt.textContent = v;
        appUpdateVersion.appendChild(opt);
      }
      const canonicalMatch = (value) => {
        const wanted = String(value || "").trim();
        if (!wanted) return "";
        for (const version of versions) {
          if (compareVersions(version, wanted) === 0) return version;
        }
        return "";
      };
      let want = canonicalMatch(selected);
      if (!want && selected) want = "";
      if (!want && previous && appVersionsBoundToChannel === channel) {
        want = canonicalMatch(previous);
      }
      appUpdateVersion.value = want;
      appVersionsBoundToChannel = channel;
    }

    async function loadAppUpdateStatus(selectedVersion = "") {
      try {
        const res = await fetch("/api/app-update?ts=" + Date.now(), { cache: "no-store" });
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "failed");
        appUpdateLoadFailures = 0;
        const info = payload.app_update || {};
        if (selectedVersion && !info.running) {
          await triggerAppUpdateCheck(selectedVersion);
          return;
        }
        appUpdateRunning = !!info.running;
        const result = info.last_result || {};
        updateAppVersionDropdown(info);
        const rel = compareVersions(result.current_version, result.latest_version);
        const shouldInstall = (!!result.should_install || !!result.is_newer || rel === -1 || rel === 1);
        const assetSupported = (result.asset_supported !== false);
        const updateAvailable = shouldInstall && assetSupported;
        const reinstallAvailable = assetSupported && (!!result && Object.keys(result).length > 0);
        const nowMs = Date.now();
        if (prevAppUpdateRunning && !info.running && !info.last_error && !updateAvailable) {
          appJustUpdatedUntil = nowMs + 12000;
        }
        if (prevAppUpdateRunning && !info.running && !info.last_error && !appUpdateRefreshScheduled) {
          appUpdateRefreshScheduled = true;
          setAppUpdateStatus("Update completed. Refreshing dashboard...", "just-updated");
          setAppUpdateProgress(100, "Update completed. Refreshing dashboard...");
          setTimeout(() => {
            window.location.reload();
          }, 1500);
        }
        prevAppUpdateRunning = !!info.running;
        const selectedCmp = compareVersions(result.current_version, result.selected_version);
        const downgrading = (selectedCmp === 1);
        let level = "updated";
        if (info.last_error) level = "error";
        else if (info.running) level = "running";
        else if (nowMs < appJustUpdatedUntil) level = "just-updated";
        else if (downgrading) level = "downgrading";
        else if (updateAvailable) level = "outdated";
        else if (!result || Object.keys(result).length === 0) level = "unknown";
        lastAppCheckAt = new Date().toLocaleTimeString();
        setAppUpdateStatus(formatAppUpdateSummary(info), level);
        const appPct = Number(info.progress_percent || 0);
        const appMsg = String(info.progress_message || "");
        setAppUpdateProgress(appPct, appMsg ? (appMsg + " (" + appPct + "%)") : (appPct > 0 ? ("Progress: " + appPct + "%") : ""));
        checkAppUpdateBtn.disabled = !!info.running;
        checkAppUpdateBtn.title = !!info.running ? "Cannot check while install is running" : "Check for app updates now";
        downloadAppUpdateBtn.disabled = !!info.running || !updateAvailable;
        if (updateAvailable) {
          downloadAppUpdateBtn.title = "Install selected app version and restart";
        } else if (shouldInstall && !assetSupported) {
          downloadAppUpdateBtn.title = "Selected release does not include a self-installable asset for this platform";
        } else {
          downloadAppUpdateBtn.title = "No app install needed";
        }
        if (reinstallAppBtn) {
          reinstallAppBtn.disabled = !!info.running || !reinstallAvailable;
          if (!reinstallAvailable) {
            reinstallAppBtn.title = "Check for app updates first";
          } else if (!assetSupported) {
            reinstallAppBtn.title = "Selected release does not include a self-installable asset for this platform";
          } else {
            reinstallAppBtn.title = "Reinstall selected app version even if unchanged";
          }
        }
      } catch (err) {
        setAppUpdateStatus("Failed to load app update status.", "error");
        appUpdateLoadFailures += 1;
        if (appUpdateRunning && appUpdateLoadFailures >= 3 && !appUpdateRefreshScheduled) {
          appUpdateRefreshScheduled = true;
          setTimeout(() => {
            window.location.reload();
          }, 1200);
        }
      }
    }

    async function triggerAppUpdateDownload(forceReinstall = false) {
      const selectedVersion = String(appUpdateVersion.value || "").trim();
      const selectedChannel = String(document.getElementById("app_update_channel").value || "release").trim();
      setAppUpdateStatus(
        forceReinstall ? "Starting app reinstall..." : "Starting app update install...",
        "running"
      );
      try {
        const res = await fetch("/api/app-update/download", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            selected_version: selectedVersion,
            channel: selectedChannel,
            force_reinstall: !!forceReinstall
          })
        });
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "failed");
        await loadAppUpdateStatus();
      } catch (err) {
        setAppUpdateStatus(
          forceReinstall ? "Failed to start app reinstall." : "Failed to start app update install.",
          "error"
        );
        setAppUpdateProgress(0, "");
      }
    }

    async function triggerAppUpdateCheck(selectedVersion = null) {
      const selected = (selectedVersion === null || selectedVersion === undefined)
        ? String(appUpdateVersion.value || "").trim()
        : String(selectedVersion || "").trim();
      const selectedChannel = String(document.getElementById("app_update_channel").value || "release").trim();
      setAppUpdateStatus("Checking app update...", "running");
      try {
        const res = await fetch("/api/app-update/check", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ selected_version: selected, channel: selectedChannel })
        });
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "failed");
        await loadAppUpdateStatus();
      } catch (err) {
        setAppUpdateStatus("Failed to check app update.", "error");
        setAppUpdateProgress(0, "");
      }
    }

    function formatBinariesSummary(b) {
      if (!b) return "No binary status available.";
      if (b.running) return b.progress_message || "Binary update is running...";
      if (b.last_error) return "Binary update error: " + b.last_error;
      const r = b.last_result || null;
      if (!r) return "Binary status not available yet.";
      const y = r["yt-dlp"] || {};
      const f = r["ffmpeg"] || {};
      const needs = [];
      if (String(y.status || "") === "update_available") needs.push("yt-dlp");
      if (String(f.status || "") === "update_available") needs.push("ffmpeg");
      let text = needs.length ? ("Updates available: " + needs.join(", ")) : "All binaries up to date";
      if (lastBinariesCheckAt) text += " | checked " + lastBinariesCheckAt;
      return text;
    }

    async function loadBinariesStatus() {
      try {
        const res = await fetch("/api/binaries?ts=" + Date.now(), { cache: "no-store" });
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "failed");
        const info = payload.binaries || {};
        binariesUpdateRunning = !!info.running;
        const result = info.last_result || {};
        const y = result["yt-dlp"] || {};
        const f = result["ffmpeg"] || {};
        const hasUnknown = (String(y.status || "unknown") === "unknown") || (String(f.status || "unknown") === "unknown");
        const updateAvailable = !!result.any_update_available;
        const nowMs = Date.now();
        if (prevBinariesUpdateRunning && !info.running && !info.last_error && !updateAvailable && !hasUnknown) {
          binariesJustUpdatedUntil = nowMs + 12000;
        }
        prevBinariesUpdateRunning = !!info.running;
        let level = "updated";
        if (info.last_error) level = "error";
        else if (info.running) level = "running";
        else if (nowMs < binariesJustUpdatedUntil) level = "just-updated";
        else if (updateAvailable) level = "outdated";
        else if (hasUnknown) level = "unknown";
        lastBinariesCheckAt = new Date().toLocaleTimeString();
        setBinariesStatus(formatBinariesSummary(info), level);
        const pct = Number(info.progress_percent || 0);
        const msg = String(info.progress_message || "");
        setBinariesProgress(pct, msg ? (msg + " (" + pct + "%)") : (pct > 0 ? ("Progress: " + pct + "%") : ""));
        updateBinariesBtn.disabled = !!info.running || (!updateAvailable && !hasUnknown);
        updateBinariesBtn.title = (updateAvailable || hasUnknown) ? "Update yt-dlp and FFmpeg" : "Binaries are already up to date";
      } catch (err) {
        setBinariesStatus("Failed to load binary status.", "error");
        setBinariesProgress(0, "");
      }
    }

    async function triggerBinariesUpdate() {
      setBinariesStatus("Starting binary update...", "running");
      try {
        const res = await fetch("/api/binaries/update", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}"
        });
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "failed");
        setBinariesProgress(1, "Preparing binary update... (1%)");
        await loadBinariesStatus();
      } catch (err) {
        setBinariesStatus("Failed to start binary update.", "error");
        setBinariesProgress(0, "");
      }
    }

    function activateTab(tabId, persist = true) {
      const wanted = String(tabId || "");
      const fallback = tabButtons.find((b) => b.classList.contains("active")) || tabButtons[0];
      const targetBtn = tabButtons.find((b) => b.dataset.tab === wanted) || fallback;
      if (!targetBtn) return;
      const targetId = String(targetBtn.dataset.tab || "");
      tabButtons.forEach((b) => b.classList.toggle("active", b === targetBtn));
      tabPanels.forEach((p) => p.classList.toggle("active", p.id === targetId));
      if (persist) {
        try { localStorage.setItem(STORAGE_TAB_KEY, targetId); } catch (err) {}
      }
    }

    function activateSubtab(tabId, persist = true) {
      const wanted = String(tabId || "");
      const fallback = subtabButtons.find((b) => b.classList.contains("active")) || subtabButtons[0];
      const targetBtn = subtabButtons.find((b) => b.dataset.subtab === wanted) || fallback;
      if (!targetBtn) return;
      const targetId = String(targetBtn.dataset.subtab || "");
      subtabButtons.forEach((b) => b.classList.toggle("active", b === targetBtn));
      subtabPanels.forEach((p) => p.classList.toggle("active", p.id === targetId));
      if (persist) {
        try { localStorage.setItem(STORAGE_SUBTAB_KEY, targetId); } catch (err) {}
      }
    }

    tabButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        activateTab(btn.dataset.tab, true);
      });
    });
    subtabButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        activateSubtab(btn.dataset.subtab, true);
      });
    });

    startBtn.addEventListener("click", () => api("/api/start"));
    stopBtn.addEventListener("click", () => api("/api/stop"));
    skipBtn.addEventListener("click", () => api("/api/skip"));
    refreshBtn.addEventListener("click", () => refreshState(true));
    reloadSettingsBtn.addEventListener("click", () => loadSettings());
    if (sourceAddBtn) {
      sourceAddBtn.addEventListener("click", () => addSourceFromInput());
    }
    if (sourceAddInput) {
      sourceAddInput.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          addSourceFromInput();
        }
      });
    }
    if (sourceAddName) {
      sourceAddName.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          addSourceFromInput();
        }
      });
    }
    // Fallback for stale partial DOM states: keep Add functional via delegation.
    document.addEventListener("click", (ev) => {
      const target = ev.target;
      if (!(target instanceof Element)) return;
      const addBtn = target.closest("#source_add_btn");
      if (!addBtn) return;
      ev.preventDefault();
      addSourceFromInput();
    });
    window.addEventListener("resize", () => {
      renderSourcesList();
      syncSourceSelect(sourcesState, String(sourceSelect.value || "").trim());
    });
    checkAppUpdateBtn.addEventListener("click", () => triggerAppUpdateCheck());
    downloadAppUpdateBtn.addEventListener("click", () => triggerAppUpdateDownload());
    if (reinstallAppBtn) {
      reinstallAppBtn.addEventListener("click", () => triggerAppUpdateDownload(true));
    }
    appUpdateVersion.addEventListener("change", () => triggerAppUpdateCheck());
    const appUpdateChannel = document.getElementById("app_update_channel");
    if (appUpdateChannel) {
      appUpdateChannel.addEventListener("change", () => {
        appVersionsBoundToChannel = "";
        appUpdateVersion.innerHTML = '<option value="">Latest in selected channel</option>';
        triggerAppUpdateCheck("");
      });
    }
    const checkUpdatesToggle = document.getElementById("check_updates_startup");
    if (checkUpdatesToggle) {
      checkUpdatesToggle.addEventListener("change", () => {
        resetAppAutoCheckTimer(true);
      });
    }
    updateBinariesBtn.addEventListener("click", () => triggerBinariesUpdate());
    bindAutoSaveListeners();
    initThemeSelector();
    try {
      activateTab(localStorage.getItem(STORAGE_TAB_KEY) || "", false);
      activateSubtab(localStorage.getItem(STORAGE_SUBTAB_KEY) || "", false);
    } catch (err) {
      activateTab("", false);
      activateSubtab("", false);
    }
    loadSettings();
    loadBinariesStatus();
    refreshState(true);
    setInterval(() => {
      refreshState(false);
      if (binariesUpdateRunning) loadBinariesStatus();
      if (appUpdateRunning) loadAppUpdateStatus();
    }, 1200);
    setInterval(() => {
      loadBinariesStatus();
    }, 300000);
