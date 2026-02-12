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
    const appUpdateStatus = document.getElementById("appUpdateStatus");
    const appUpdateVersion = document.getElementById("app_update_version");
    const updateBinariesBtn = document.getElementById("updateBinariesBtn");
    const binariesStatus = document.getElementById("binariesStatus");
    const binariesProgressBar = document.getElementById("binariesProgressBar");
    const binariesProgressText = document.getElementById("binariesProgressText");
    const sourcesInput = document.getElementById("sources_input");
    const sourceHint = document.getElementById("sourceHint");
    const sourceSelect = document.getElementById("playlist_url");
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
    const fieldIds = ["playlist_url", "rtmp_base", "stream_key", "resolution", "framerate", "video_bitrate", "buffer_mode", "encoder_preference", "yt_auth_enabled", "yt_auth_browser", "yt_auth_profile", "overlay_titles", "shuffle", "log_to_file", "remember", "check_updates_startup", "auto_app_updates", "app_update_channel", "update_download_cap_mbps"];

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

    function syncSourceSelect(sources, preferred) {
      const safeSources = normalizeSources(sources);
      sourceSelect.innerHTML = "";
      if (!safeSources.length) {
        const emptyOption = document.createElement("option");
        emptyOption.value = "";
        emptyOption.textContent = "No sources configured";
        sourceSelect.appendChild(emptyOption);
        sourceSelect.value = "";
        sourceSelect.disabled = true;
        sourceHint.textContent = "Add source URLs in the Sources tab.";
        return "";
      }
      safeSources.forEach((src, idx) => {
        const opt = document.createElement("option");
        opt.value = src;
        opt.textContent = "Source " + (idx + 1) + " - " + src;
        sourceSelect.appendChild(opt);
      });
      const wanted = safeSources.includes(preferred) ? preferred : safeSources[0];
      sourceSelect.value = wanted;
      sourceSelect.disabled = (safeSources.length <= 1);
      sourceHint.textContent = (safeSources.length <= 1)
        ? "Only one source is configured. It will be used automatically."
        : "Choose which source to stream.";
      return wanted;
    }

    function syncSourcesUIFromSettings(settings) {
      const sources = normalizeSources(settings.sources);
      const fallbackPlaylist = String(settings.playlist_url || "").trim();
      if (!sources.length && fallbackPlaylist) sources.push(fallbackPlaylist);
      sourcesInput.value = sources.join("\n");
      return syncSourceSelect(sources, fallbackPlaylist);
    }

    for (let kbps = 1500; kbps <= 25000; kbps += 500) {
      const o = document.createElement("option");
      o.value = String(kbps) + "k";
      o.textContent = String(kbps) + " kbps";
      document.getElementById("video_bitrate").appendChild(o);
    }

    for (let i = 1; i <= 25; i++) {
      const o = document.createElement("option");
      o.value = String(i);
      o.textContent = String(i);
      document.getElementById("update_download_cap_mbps").appendChild(o);
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

    function setBinariesProgress(percent, text) {
      const p = Math.max(0, Math.min(100, Number(percent) || 0));
      binariesProgressBar.style.width = p + "%";
      binariesProgressText.textContent = text || (p > 0 ? ("Progress: " + p + "%") : "");
    }

    function versionTuple(v) {
      const m = String(v || "").match(/(\\d+(?:\\.\\d+)+)/);
      if (!m) return null;
      return m[1].split(".").map((x) => Number(x));
    }

    function compareVersions(current, latest) {
      const a = versionTuple(current);
      const b = versionTuple(latest);
      if (!a || !b) return null;
      const n = Math.max(a.length, b.length);
      for (let i = 0; i < n; i++) {
        const av = a[i] || 0;
        const bv = b[i] || 0;
        if (av > bv) return 1;
        if (av < bv) return -1;
      }
      return 0;
    }

    async function api(path) {
      await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await refreshState(true);
    }

    function updateLogBox(el, arr, forceScroll) {
      const logs = Array.isArray(arr) ? arr : [];
      const text = logs.join("\\n");
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
        if (meta.source) parts.push("source: " + meta.source);
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
    }

    function formToPayload() {
      const out = {};
      const currentSources = normalizeSources(sourcesInput.value);
      const selected = String(sourceSelect.value || "").trim();
      const normalizedSelected = syncSourceSelect(currentSources, selected);
      for (const id of fieldIds) {
        const el = document.getElementById(id);
        if (!el) continue;
        out[id] = (el.type === "checkbox") ? !!el.checked : el.value;
      }
      out.sources = currentSources;
      if (currentSources.length === 1) {
        out.playlist_url = currentSources[0];
      } else if (!currentSources.length) {
        out.playlist_url = "";
      } else if (!currentSources.includes(String(out.playlist_url || ""))) {
        out.playlist_url = normalizedSelected || currentSources[0];
      }
      out.framerate = Number(out.framerate || 30);
      out.update_download_cap_mbps = Number(out.update_download_cap_mbps || 25);
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
      if (sourcesInput) {
        sourcesInput.addEventListener("input", () => {
          syncSourceSelect(normalizeSources(sourcesInput.value), sourceSelect.value);
          queueAutoSave(650);
        });
        sourcesInput.addEventListener("change", () => {
          syncSourceSelect(normalizeSources(sourcesInput.value), sourceSelect.value);
          queueAutoSave(180);
        });
      }
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
      } catch (err) {
        setSettingsStatus("Failed to load settings.", "err");
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
      let want = selected || "";
      if (want && !versions.includes(want)) want = "";
      if (!want && previous && appVersionsBoundToChannel === channel && versions.includes(previous)) {
        want = previous;
      }
      appUpdateVersion.value = want;
      appVersionsBoundToChannel = channel;
    }

    async function loadAppUpdateStatus(selectedVersion = "") {
      try {
        const res = await fetch("/api/app-update?ts=" + Date.now(), { cache: "no-store" });
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "failed");
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
        const nowMs = Date.now();
        if (prevAppUpdateRunning && !info.running && !info.last_error && !updateAvailable) {
          appJustUpdatedUntil = nowMs + 12000;
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
      } catch (err) {
        setAppUpdateStatus("Failed to load app update status.", "error");
      }
    }

    async function triggerAppUpdateDownload() {
      const selectedVersion = String(appUpdateVersion.value || "").trim();
      const selectedChannel = String(document.getElementById("app_update_channel").value || "release").trim();
      setAppUpdateStatus("Starting app update install...", "running");
      try {
        const res = await fetch("/api/app-update/download", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ selected_version: selectedVersion, channel: selectedChannel })
        });
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "failed");
        await loadAppUpdateStatus();
      } catch (err) {
        setAppUpdateStatus("Failed to start app update install.", "error");
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

    tabButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const tabId = btn.dataset.tab;
        tabButtons.forEach((b) => b.classList.toggle("active", b === btn));
        tabPanels.forEach((p) => p.classList.toggle("active", p.id === tabId));
      });
    });
    subtabButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const tabId = btn.dataset.subtab;
        subtabButtons.forEach((b) => b.classList.toggle("active", b === btn));
        subtabPanels.forEach((p) => p.classList.toggle("active", p.id === tabId));
      });
    });

    startBtn.addEventListener("click", () => api("/api/start"));
    stopBtn.addEventListener("click", () => api("/api/stop"));
    skipBtn.addEventListener("click", () => api("/api/skip"));
    refreshBtn.addEventListener("click", () => refreshState(true));
    reloadSettingsBtn.addEventListener("click", () => loadSettings());
    checkAppUpdateBtn.addEventListener("click", () => triggerAppUpdateCheck());
    downloadAppUpdateBtn.addEventListener("click", () => triggerAppUpdateDownload());
    appUpdateVersion.addEventListener("change", () => triggerAppUpdateCheck());
    document.getElementById("app_update_channel").addEventListener("change", () => {
      appVersionsBoundToChannel = "";
      appUpdateVersion.innerHTML = '<option value="">Latest in selected channel</option>';
      triggerAppUpdateCheck("");
    });
    updateBinariesBtn.addEventListener("click", () => triggerBinariesUpdate());
    bindAutoSaveListeners();
    loadSettings();
    loadAppUpdateStatus();
    loadBinariesStatus();
    refreshState(true);
    setInterval(() => {
      refreshState(false);
      if (binariesUpdateRunning) loadBinariesStatus();
      if (appUpdateRunning) loadAppUpdateStatus();
    }, 1200);
    setInterval(() => {
      loadAppUpdateStatus();
      loadBinariesStatus();
    }, 300000);
