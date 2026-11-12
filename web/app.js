    const stateText = document.getElementById("stateText");
    const metaText = document.getElementById("metaText");
    const otherLogBox = document.getElementById("otherLogBox");
    const ffmpegLogBox = document.getElementById("ffmpegLogBox");
    const startBtn = document.getElementById("startBtn");
    const restartBtn = document.getElementById("restartBtn");
    let actionPending = false;
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
    const streamKeyAddName = document.getElementById("stream_key_add_name");
    const streamKeyAddInput = document.getElementById("stream_key_add_input");
    const streamKeyAddBtn = document.getElementById("stream_key_add_btn");
    const streamKeysList = document.getElementById("stream_keys_list");
    const streamKeysEmpty = document.getElementById("stream_keys_empty");
    const streamKeyHint = document.getElementById("streamKeyHint");
    const streamKeySelect = document.getElementById("stream_key");
    const streamUrlPreset = document.getElementById("stream_url_preset");
    const streamPlatformHint = document.getElementById("streamPlatformHint");
    const streamPlatformAddName = document.getElementById("stream_platform_add_name");
    const streamPlatformAddInput = document.getElementById("stream_platform_add_input");
    const streamPlatformAddBtn = document.getElementById("stream_platform_add_btn");
    const streamPlatformsList = document.getElementById("stream_platforms_list");
    const streamPlatformsEmpty = document.getElementById("stream_platforms_empty");
    const themeSelect = document.getElementById("theme_select");
    const tabButtons = Array.from(document.querySelectorAll(".tab-btn"));
    const tabPanels = Array.from(document.querySelectorAll(".tab-panel"));
    const subtabButtons = Array.from(document.querySelectorAll(".subtab-btn"));
    const subtabPanels = Array.from(document.querySelectorAll(".subtab-panel"));
    let busy = false;
    let streamActive = false;
    let streamStateKnown = false;
    let appUpdateRunning = false;
    let binariesUpdateRunning = false;
    let prevBinariesUpdateRunning = false;
    let binariesJustUpdatedUntil = 0;
    let lastAppCheckAt = "";
    let lastBinariesCheckAt = "";
    let suppressAutoSave = false;
    let autoSaveTimer = null;
    let saveInFlight = false;
    let pendingAutoSave = false;
    let lastSavedPayload = "";
    let bitrateEditRevision = 0;
    let appVersionsBoundToChannel = "";
    const APP_UPDATE_AUTO_CHECK_MS = 10 * 60 * 1000;
    let appAutoCheckTimer = null;
    let appUpdateRefreshScheduled = false;
    let appRequestedVersion = "";
    let savedAutoInstallEnabled = false;
    let appInstallPending = false;
    let appInstallInstance = "";
    let appUpdateStatusLoading = false;
    const STORAGE_TAB_KEY = "stream247.active_tab";
    const STORAGE_SUBTAB_KEY = "stream247.active_subtab";
    const fieldIds = ["playlist_url", "resolution", "framerate", "video_bitrate", "twitch_enhanced_broadcasting", "buffer_mode", "encoder_preference", "overlay_titles", "shuffle", "log_to_file", "ffmpeg_log_to_file", "debug_logging", "remember", "check_updates_startup", "auto_app_updates", "app_update_channel"];
    const STREAM_URL_PRESETS = {
      youtube: { name: "YouTube", rtmp_base: "rtmp://a.rtmp.youtube.com/live2" },
      twitch: { name: "Twitch", rtmp_base: "rtmp://live.twitch.tv/app" },
      kick: { name: "Kick", rtmp_base: "rtmps://fa723fc1b171.global-contribute.live-video.net:443/app" },
      facebook: { name: "Facebook Live", rtmp_base: "rtmps://live-api-s.facebook.com:443/rtmp/" },
      tiktok: { name: "TikTok Live", rtmp_base: "rtmps://push-rtmp-global.tiktok.com/live/" },
      trovo: { name: "Trovo", rtmp_base: "rtmp://livepush.trovo.live/live/" }
    };
    let streamUrlServicesState = [];

    let sourcesState = [];
    let streamKeysState = [];

    function normalizeStreamUrl(url) {
      return String(url || "").trim().replace(/\/+$/, "");
    }

    function streamServiceOptionValue(entry) {
      const url = String(entry && entry.rtmp_base || "").trim();
      return url ? ("saved:" + url) : "";
    }

    function presetUrlForKey(key) {
      const preset = STREAM_URL_PRESETS[String(key || "").trim()];
      return String(preset && preset.rtmp_base || "").trim();
    }

    function normalizeStreamServiceEntries(value) {
      const out = [];
      const seen = new Set();
      const push = (rawName, rawUrl) => {
        const name = String(rawName || "").trim();
        const rtmpBase = String(rawUrl || "").trim();
        const normalizedUrl = normalizeStreamUrl(rtmpBase);
        if (!name || !rtmpBase || !normalizedUrl || seen.has(normalizedUrl)) return;
        seen.add(normalizedUrl);
        out.push({ name, rtmp_base: rtmpBase });
      };
      if (Array.isArray(value)) {
        value.forEach((item) => {
          if (!item || typeof item !== "object" || Array.isArray(item)) return;
          push(item.name || item.label || item.title || "", item.rtmp_base || item.url || item.value || "");
        });
      } else if (value && typeof value === "object") {
        for (const [rawName, rawUrl] of Object.entries(value)) {
          push(rawName, rawUrl);
        }
      }
      return out;
    }

    function setStreamUrlServicesState(nextServices) {
      streamUrlServicesState = normalizeStreamServiceEntries(nextServices);
      renderStreamPlatformsList();
    }

    function savedServiceForOption(optionValue) {
      const raw = String(optionValue || "").trim();
      if (!raw.startsWith("saved:")) return null;
      const wanted = normalizeStreamUrl(raw.slice(6));
      if (!wanted) return null;
      return streamUrlServicesState.find((entry) => normalizeStreamUrl(entry.rtmp_base) === wanted) || null;
    }

    function rebuildStreamPresetOptions(preferredValue) {
      if (!streamUrlPreset) return;
      const wanted = String(preferredValue || streamUrlPreset.value || "").trim();
      streamUrlPreset.innerHTML = "";
      for (const [presetKey, presetMeta] of Object.entries(STREAM_URL_PRESETS)) {
        const opt = document.createElement("option");
        opt.value = presetKey;
        opt.textContent = String(presetMeta.name || presetKey);
        streamUrlPreset.appendChild(opt);
      }
      streamUrlServicesState.forEach((entry) => {
        const opt = document.createElement("option");
        opt.value = streamServiceOptionValue(entry);
        opt.textContent = String(entry.name || "Saved Service");
        opt.title = String(entry.rtmp_base || "");
        streamUrlPreset.appendChild(opt);
      });
      const canUseWanted = Array.from(streamUrlPreset.options).some((opt) => opt.value === wanted);
      streamUrlPreset.value = canUseWanted ? wanted : "youtube";
      syncStreamPlatformHint();
    }

    function streamPlatformLabel(entry) {
      const name = String(entry && entry.name || "").trim();
      return name || "Custom Platform";
    }

    function renderStreamPlatformsList() {
      if (!streamPlatformsList || !streamPlatformsEmpty) return;
      streamPlatformsList.innerHTML = "";
      if (!streamUrlServicesState.length) {
        streamPlatformsEmpty.style.display = "";
        return;
      }
      streamPlatformsEmpty.style.display = "none";
      streamUrlServicesState.forEach((entry, idx) => {
        const row = document.createElement("div");
        row.className = "source-item";

        const main = document.createElement("div");
        main.className = "source-main";

        const name = document.createElement("div");
        name.className = "source-name";
        name.textContent = streamPlatformLabel(entry);

        const url = document.createElement("div");
        url.className = "source-url";
        url.textContent = truncateUrl(entry.rtmp_base);
        url.title = String(entry.rtmp_base || "");

        const copyBtn = document.createElement("button");
        copyBtn.className = "source-copy";
        copyBtn.type = "button";
        copyBtn.textContent = "Copy URL";
        copyBtn.addEventListener("click", async () => {
          const ok = await copyTextToClipboard(entry.rtmp_base);
          setSettingsStatus(ok ? "Copied platform URL to clipboard." : "Failed to copy platform URL.", ok ? "ok" : "err");
        });

        const removeBtn = document.createElement("button");
        removeBtn.className = "source-remove";
        removeBtn.disabled = streamSettingsLocked();
        removeBtn.type = "button";
        removeBtn.textContent = "Remove";
        removeBtn.addEventListener("click", () => {
          const nextServices = streamUrlServicesState.filter((_, i) => i !== idx);
          setStreamUrlServicesState(nextServices);
          const selected = String(streamUrlPreset && streamUrlPreset.value || "").trim();
          const current = savedServiceForOption(selected);
          if (!current) {
            rebuildStreamPresetOptions(selected || "youtube");
          } else {
            const currentNormalized = normalizeStreamUrl(current.rtmp_base);
            const stillExists = nextServices.some((item) => normalizeStreamUrl(item.rtmp_base) === currentNormalized);
            rebuildStreamPresetOptions(stillExists ? selected : "youtube");
          }
          queueAutoSave(180);
        });

        main.appendChild(name);
        main.appendChild(url);
        row.appendChild(main);
        row.appendChild(copyBtn);
        row.appendChild(removeBtn);
        streamPlatformsList.appendChild(row);
      });
    }

    function streamSettingsLocked() {
      return !streamStateKnown || streamActive || actionPending;
    }

    function syncStreamSettingsLock() {
      const locked = streamSettingsLocked();
      for (const id of ["resolution", "framerate", "buffer_mode", "encoder_preference", "overlay_titles", "shuffle", "stream_url_preset"]) {
        document.getElementById(id).disabled = locked;
      }
      sourceSelect.disabled = locked || sourceSelect.options.length <= 1;
      streamKeySelect.disabled = locked || streamKeySelect.options.length <= 1;
      // Adding/removing saved entries can also change the selected source or destination.
      for (const el of document.querySelectorAll("#tab-sources input, #tab-sources .source-remove, #source_add_btn, #tab-stream-keys input, #tab-stream-keys .source-remove, #stream_key_add_btn, #tab-stream-platforms input, #tab-stream-platforms .source-remove, #stream_platform_add_btn")) {
        el.disabled = locked;
      }
      document.querySelectorAll(".stream-settings-lock-note").forEach(note => {
        note.hidden = !locked;
        note.textContent = !streamStateKnown
          ? "Stream settings are locked until stream status is available."
          : "Stream settings are locked. Stop the stream to edit them.";
      });
      syncEnhancedBroadcastingUI();
    }

    function syncEnhancedBroadcastingUI() {
      const group = document.getElementById("twitchEnhancedGroup");
      const enabled = document.getElementById("twitch_enhanced_broadcasting");
      if (!group || !enabled) return;
      const selected = String(streamUrlPreset && streamUrlPreset.value || "");
      const saved = savedServiceForOption(selected);
      const url = saved ? String(saved.rtmp_base || "") : presetUrlForKey(selected);
      let twitch = false;
      try {
        const host = new URL(url).hostname.toLowerCase();
        twitch = host === "twitch.tv" || host.endsWith(".twitch.tv");
      } catch (err) {}
      group.hidden = !twitch;
      const locked = streamSettingsLocked();
      enabled.disabled = locked || !twitch;
      const active = twitch && enabled.checked;
      const videoBitrate = document.getElementById("video_bitrate");
      videoBitrate.disabled = locked || active;
      videoBitrate.title = locked ? "Stop the stream to edit the bitrate." : active ? "Disable Enhanced Broadcasting to edit the video bitrate." : "";
      document.getElementById("twitchEnhancedOptions").hidden = !twitch;
      const tracksAuto = document.getElementById("twitch_enhanced_tracks_auto");
      const bitrateAuto = document.getElementById("twitch_enhanced_bitrate_auto");
      tracksAuto.disabled = locked || !active;
      bitrateAuto.disabled = locked || !active;
      document.getElementById("twitch_enhanced_max_tracks").disabled = locked || !active || tracksAuto.checked;
      document.getElementById("twitch_enhanced_max_bitrate_kbps").disabled = locked || !active || bitrateAuto.checked;
    }

    function syncStreamPlatformHint() {
      syncStreamSettingsLock();
      if (!streamPlatformHint || !streamUrlPreset) return;
      const selected = String(streamUrlPreset.value || "").trim();
      const saved = savedServiceForOption(selected);
      if (saved) {
        streamPlatformHint.textContent = String(saved.rtmp_base || "").trim();
        return;
      }
      const presetUrl = presetUrlForKey(selected);
      streamPlatformHint.textContent = presetUrl || "";
    }

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

    function normalizeStreamKeys(value) {
      const out = [];
      const seen = new Set();
      const push = (raw) => {
        const key = String(raw || "").trim();
        if (!key || seen.has(key)) return;
        seen.add(key);
        out.push(key);
      };
      if (Array.isArray(value)) {
        value.forEach((item) => {
          if (!item || typeof item !== "object" || Array.isArray(item)) {
            push(item);
            return;
          }
          push(item.stream_key || item.key || item.value || "");
        });
      } else if (typeof value === "string") {
        value.split(/\r?\n/g).forEach(push);
      }
      return out;
    }

    function normalizeStreamKeyNames(value) {
      const out = {};
      if (!value || typeof value !== "object" || Array.isArray(value)) return out;
      for (const [rawKey, rawName] of Object.entries(value)) {
        const streamKey = String(rawKey || "").trim();
        const name = String(rawName || "").trim();
        if (!streamKey || !name) continue;
        out[streamKey] = name;
      }
      return out;
    }

    function normalizeStreamKeyEntries(value, streamKeyNames) {
      const keys = normalizeStreamKeys(value);
      const namesMap = normalizeStreamKeyNames(streamKeyNames);
      if (Array.isArray(value)) {
        value.forEach((item) => {
          if (!item || typeof item !== "object" || Array.isArray(item)) return;
          const streamKey = String(item.stream_key || item.key || item.value || "").trim();
          const name = String(item.name || "").trim();
          if (streamKey && name && !namesMap[streamKey]) namesMap[streamKey] = name;
        });
      }
      return keys.map((streamKey) => ({ key: streamKey, name: namesMap[streamKey] || "" }));
    }

    function truncateUrl(url) {
      const src = String(url || "").trim();
      const limit = (window.innerWidth <= 700) ? 36 : 70;
      if (src.length <= limit) return src;
      return src.slice(0, Math.max(8, limit - 3)) + "...";
    }

    function maskStreamKey(streamKey) {
      const key = String(streamKey || "").trim();
      if (!key) return "";
      if (key.length <= 4) return "*".repeat(key.length);
      return "*".repeat(Math.max(3, key.length - 4)) + key.slice(-4);
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

    function streamKeyLabel(entry) {
      const streamKey = String(entry && entry.key || "").trim();
      const name = String(entry && entry.name || "").trim();
      if (!streamKey) return "";
      if (name) return name;
      return maskStreamKey(streamKey);
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

    function normalizeStreamKeyStateEntries(value) {
      const raw = Array.isArray(value) ? value : [];
      const byKey = new Map();
      raw.forEach((entry) => {
        const streamKey = String(entry && entry.key || "").trim();
        if (!streamKey) return;
        const name = String(entry && entry.name || "").trim();
        if (!byKey.has(streamKey)) {
          byKey.set(streamKey, { key: streamKey, name });
        } else if (name && !String(byKey.get(streamKey).name || "").trim()) {
          byKey.get(streamKey).name = name;
        }
      });
      return Array.from(byKey.values());
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
        removeBtn.disabled = streamSettingsLocked();
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
      sourceSelect.disabled = streamSettingsLocked() || safeEntries.length <= 1;
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

    function renderStreamKeysList() {
      streamKeysList.innerHTML = "";
      if (!streamKeysState.length) {
        streamKeysEmpty.style.display = "";
        return;
      }
      streamKeysEmpty.style.display = "none";
      streamKeysState.forEach((entry, idx) => {
        const row = document.createElement("div");
        row.className = "source-item";

        const main = document.createElement("div");
        main.className = "source-main";

        const name = document.createElement("div");
        name.className = "source-name";
        name.textContent = String(entry.name || "").trim() || "Unnamed Stream Key";

        const keyText = document.createElement("div");
        keyText.className = "source-url";
        keyText.textContent = maskStreamKey(entry.key);
        keyText.title = "Ends with " + String(entry.key || "").slice(-4);

        const copyBtn = document.createElement("button");
        copyBtn.className = "source-copy";
        copyBtn.type = "button";
        copyBtn.textContent = "Copy Key";
        copyBtn.addEventListener("click", async () => {
          const ok = await copyTextToClipboard(entry.key);
          if (ok) {
            setSettingsStatus("Copied stream key to clipboard.", "ok");
          } else {
            setSettingsStatus("Failed to copy stream key.", "err");
          }
        });

        const removeBtn = document.createElement("button");
        removeBtn.className = "source-remove";
        removeBtn.disabled = streamSettingsLocked();
        removeBtn.type = "button";
        removeBtn.textContent = "Remove";
        removeBtn.addEventListener("click", () => {
          const nextKeys = streamKeysState.filter((_, i) => i !== idx);
          const selected = String(streamKeySelect.value || "").trim();
          const preferred = (selected && selected !== entry.key) ? selected : "";
          setStreamKeysState(nextKeys, preferred);
          queueAutoSave(180);
        });

        main.appendChild(name);
        main.appendChild(keyText);
        row.appendChild(main);
        row.appendChild(copyBtn);
        row.appendChild(removeBtn);
        streamKeysList.appendChild(row);
      });
    }

    function syncStreamKeySelect(entries, preferred) {
      const safeEntries = normalizeStreamKeyStateEntries(entries);
      streamKeySelect.innerHTML = "";
      if (!safeEntries.length) {
        const emptyOption = document.createElement("option");
        emptyOption.value = "";
        emptyOption.textContent = "No stream keys configured";
        streamKeySelect.appendChild(emptyOption);
        streamKeySelect.value = "";
        streamKeySelect.disabled = true;
        streamKeyHint.textContent = "Add stream keys in the Stream Keys tab.";
        return "";
      }
      safeEntries.forEach((entry) => {
        const opt = document.createElement("option");
        opt.value = entry.key;
        opt.textContent = streamKeyLabel(entry);
        streamKeySelect.appendChild(opt);
      });
      const keys = safeEntries.map((entry) => entry.key);
      const wanted = keys.includes(preferred) ? preferred : keys[0];
      streamKeySelect.value = wanted;
      streamKeySelect.disabled = streamSettingsLocked() || safeEntries.length <= 1;
      streamKeyHint.textContent = (safeEntries.length <= 1)
        ? "Only one stream key is configured. It will be used automatically."
        : "Choose which stream key to use.";
      return wanted;
    }

    function setStreamKeysState(nextStreamKeys, preferred) {
      streamKeysState = normalizeStreamKeyStateEntries(nextStreamKeys);
      const selected = syncStreamKeySelect(streamKeysState, preferred);
      renderStreamKeysList();
      return selected;
    }

    function addStreamKeyFromInput() {
      const rawStreamKey = String(streamKeyAddInput.value || "").trim();
      const rawName = String(streamKeyAddName && streamKeyAddName.value || "").trim();
      if (!rawStreamKey) return;
      const selected = String(streamKeySelect.value || "").trim();
      const nextKeys = [{ key: rawStreamKey, name: rawName }];
      streamKeysState.forEach((entry) => {
        if (entry.key === rawStreamKey) return;
        nextKeys.push({ key: entry.key, name: entry.name });
      });
      const preferred = selected && selected !== rawStreamKey ? selected : rawStreamKey;
      setStreamKeysState(nextKeys, preferred);
      streamKeyAddInput.value = "";
      if (streamKeyAddName) streamKeyAddName.value = "";
      queueAutoSave(180);
    }

    function syncStreamKeysUIFromSettings(settings) {
      const keys = normalizeStreamKeyEntries(settings.stream_keys, settings.stream_key_names);
      const fallbackStreamKey = String(settings.stream_key || "").trim();
      if (!keys.length && fallbackStreamKey) {
        keys.push({ key: fallbackStreamKey, name: "" });
      }
      return setStreamKeysState(keys, fallbackStreamKey);
    }

    function streamPresetFromUrl(url) {
      const wanted = normalizeStreamUrl(url);
      if (!wanted) return "youtube";
      for (const [presetKey, presetMeta] of Object.entries(STREAM_URL_PRESETS)) {
        if (normalizeStreamUrl(presetMeta.rtmp_base) === wanted) return presetKey;
      }
      const savedMatch = streamUrlServicesState.find((entry) => normalizeStreamUrl(entry.rtmp_base) === wanted);
      if (savedMatch) return streamServiceOptionValue(savedMatch);
      return "";
    }

    function defaultStreamServiceName(url) {
      const rawUrl = String(url || "").trim();
      if (!rawUrl) return "Custom Service";
      try {
        const parsed = new URL(rawUrl);
        const host = String(parsed.hostname || "").trim();
        if (!host) return "Custom Service";
        return host.replace(/^www\./i, "");
      } catch (err) {
        return "Custom Service";
      }
    }

    function addStreamPlatformFromInput() {
      const rtmpBase = String(streamPlatformAddInput && streamPlatformAddInput.value || "").trim();
      if (!rtmpBase) {
        setSettingsStatus("Enter a platform RTMP URL first.", "warn");
        return;
      }
      const normalizedUrl = normalizeStreamUrl(rtmpBase);
      if (!normalizedUrl) {
        setSettingsStatus("Enter a valid platform RTMP URL first.", "warn");
        return;
      }
      const inputName = String(streamPlatformAddName && streamPlatformAddName.value || "").trim();
      const serviceName = inputName || defaultStreamServiceName(rtmpBase);
      if (!serviceName) {
        setSettingsStatus("Enter a platform name to save this URL.", "warn");
        return;
      }
      const nextServices = [{ name: serviceName, rtmp_base: rtmpBase }];
      streamUrlServicesState.forEach((entry) => {
        if (normalizeStreamUrl(entry.rtmp_base) === normalizedUrl) return;
        nextServices.push({ name: String(entry.name || "").trim(), rtmp_base: String(entry.rtmp_base || "").trim() });
      });
      setStreamUrlServicesState(nextServices);
      const selectedValue = "saved:" + rtmpBase;
      rebuildStreamPresetOptions(selectedValue);
      streamUrlPreset.value = selectedValue;
      if (streamPlatformAddName) streamPlatformAddName.value = "";
      if (streamPlatformAddInput) streamPlatformAddInput.value = "";
      queueAutoSave(180);
      setSettingsStatus("Custom stream platform saved.", "ok");
    }

    function syncStreamPresetFromRtmpBase(rtmpBase) {
      const preset = streamPresetFromUrl(rtmpBase);
      rebuildStreamPresetOptions(preset || "youtube");
      if (streamUrlPreset) streamUrlPreset.value = preset || "youtube";
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
        triggerAppUpdateCheck(appRequestedVersion);
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

    async function postAction(path) {
      const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const result = await response.json();
      if (!response.ok || result.ok === false) throw new Error(result.error || "Stream action failed.");
    }

    function syncActionButtons() {
      syncStreamSettingsLock();
      startBtn.disabled = !streamStateKnown || actionPending || streamActive;
      restartBtn.disabled = !streamStateKnown || actionPending || !streamActive;
      stopBtn.disabled = !streamStateKnown || actionPending || !streamActive;
      skipBtn.disabled = !streamStateKnown || actionPending || !streamActive;
    }

    async function api(path) {
      if (actionPending) return;
      actionPending = true;
      syncActionButtons();
      try {
        await postAction(path);
      } catch (err) {
        setSettingsStatus(err.message || "Unable to reach the server.", "err");
      } finally {
        actionPending = false;
        await refreshState(true);
        syncActionButtons();
      }
    }

    async function restartStream() {
      if (actionPending || !streamActive) return;
      actionPending = true;
      syncActionButtons();
      setSettingsStatus("Restarting stream…", "");
      try {
        await postAction("/api/stop");
        const deadline = Date.now() + 60000;
        while (true) {
          const response = await fetch("/api/state?ts=" + Date.now(), { cache: "no-store" });
          if (!response.ok) throw new Error("Unable to check stream shutdown.");
          const state = await response.json();
          if (!state.streaming) break;
          if (Date.now() >= deadline) throw new Error("Stream is still stopping. Start it again once shutdown finishes.");
          await new Promise(resolve => setTimeout(resolve, 500));
        }
        await postAction("/api/start");
        setSettingsStatus("Stream restarted.", "ok");
      } catch (err) {
        setSettingsStatus(err.message || "Unable to restart the stream.", "err");
      } finally {
        actionPending = false;
        await refreshState(true);
        syncActionButtons();
      }
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
        streamStateKnown = true;
        streamActive = streaming;
        syncStreamSettingsLock();
        stateText.textContent = s.status || "Unknown";
        stateText.className = "status" + (streaming ? " on" : "");
        const meta = s.meta || {};
        const runtimeSourceUrl = String(meta.source || "").trim();
        const selectedSourceUrl = String(sourceSelect && sourceSelect.value || "").trim();
        const firstSourceUrl = String((sourcesState[0] && sourcesState[0].url) || "").trim();
        const sourceUrl = runtimeSourceUrl || selectedSourceUrl || firstSourceUrl;
        metaText.textContent = sourceUrl ? (sourceNameForUrl(sourceUrl) || sourceUrl) : "No source selected";
        updateLogBox(otherLogBox, s.logs_other, forceScroll);
        updateLogBox(ffmpegLogBox, s.logs_ffmpeg, forceScroll);
        syncActionButtons();
      } catch (err) {
        streamStateKnown = false;
        syncActionButtons();
        stateText.textContent = "Dashboard disconnected";
        stateText.className = "status";
      } finally {
        busy = false;
      }
    }

    function applySettingsToForm(s, preserveBitrate = false) {
      const selectedSource = syncSourcesUIFromSettings(s || {});
      const selectedStreamKey = syncStreamKeysUIFromSettings(s || {});
      setStreamUrlServicesState((s && s.stream_url_services) || []);
      const currentRtmpBase = String((s && s.rtmp_base) || "").trim();
      if (currentRtmpBase && !streamPresetFromUrl(currentRtmpBase)) {
        const importedName = defaultStreamServiceName(currentRtmpBase);
        const nextServices = [{ name: importedName, rtmp_base: currentRtmpBase }];
        streamUrlServicesState.forEach((entry) => {
          nextServices.push({ name: entry.name, rtmp_base: entry.rtmp_base });
        });
        setStreamUrlServicesState(nextServices);
      }
      for (const id of fieldIds) {
        const el = document.getElementById(id);
        if (!el || !(id in s)) continue;
        if (id === "video_bitrate" && (preserveBitrate || document.activeElement === el)) continue;
        if (el.type === "checkbox") {
          el.checked = !!s[id];
        } else if (id === "playlist_url") {
          el.value = selectedSource || String(s[id] ?? "");
        } else {
          el.value = String(s[id] ?? "");
        }
      }
      if (streamKeySelect) {
        streamKeySelect.value = selectedStreamKey || String(s.stream_key ?? "");
      }
      if (s && Object.prototype.hasOwnProperty.call(s, "theme")) {
        applyTheme(String(s.theme || "purple"));
      }
      syncStreamPresetFromRtmpBase(currentRtmpBase);
      const tracks = Number(s.twitch_enhanced_max_tracks || 0);
      const bandwidth = Number(s.twitch_enhanced_max_bitrate_kbps || 0);
      document.getElementById("twitch_enhanced_tracks_auto").checked = tracks === 0;
      document.getElementById("twitch_enhanced_bitrate_auto").checked = bandwidth === 0;
      document.getElementById("twitch_enhanced_max_tracks").value = tracks || 3;
      document.getElementById("twitch_enhanced_max_bitrate_kbps").value = bandwidth || 12000;
      syncStreamSettingsLock();
      syncDebugVisibility();
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
      const currentStreamKeys = streamKeysState.map((entry) => String(entry.key || "").trim()).filter(Boolean);
      const streamKeyNames = {};
      streamKeysState.forEach((entry) => {
        const streamKey = String(entry.key || "").trim();
        const name = String(entry.name || "").trim();
        if (streamKey && name) streamKeyNames[streamKey] = name;
      });
      const selected = String(sourceSelect.value || "").trim();
      const normalizedSelected = syncSourceSelect(sourcesState, selected);
      const selectedStreamKey = String(streamKeySelect.value || "").trim();
      const normalizedStreamKey = syncStreamKeySelect(streamKeysState, selectedStreamKey);
      for (const id of fieldIds) {
        const el = document.getElementById(id);
        if (!el) continue;
        out[id] = (el.type === "checkbox") ? !!el.checked : el.value;
      }
      out.twitch_enhanced_max_tracks = document.getElementById("twitch_enhanced_tracks_auto").checked ? 0 : Number(document.getElementById("twitch_enhanced_max_tracks").value);
      out.twitch_enhanced_max_bitrate_kbps = document.getElementById("twitch_enhanced_bitrate_auto").checked ? 0 : Number(document.getElementById("twitch_enhanced_max_bitrate_kbps").value);
      if (streamUrlPreset) {
        const selectedPreset = String(streamUrlPreset.value || "youtube");
        const selectedSavedService = savedServiceForOption(selectedPreset);
        if (selectedSavedService) {
          out.rtmp_base = String(selectedSavedService.rtmp_base || "").trim();
        } else {
          out.rtmp_base = presetUrlForKey(selectedPreset) || presetUrlForKey("youtube");
        }
      } else {
        out.rtmp_base = presetUrlForKey("youtube");
      }
      out.theme = String(themeSelect && themeSelect.value || "purple").trim().toLowerCase();
      out.stream_url_services = streamUrlServicesState.map((entry) => ({
        name: String(entry.name || "").trim(),
        rtmp_base: String(entry.rtmp_base || "").trim()
      })).filter((entry) => entry.name && entry.rtmp_base);
      out.sources = currentSources;
      out.source_names = sourceNames;
      out.stream_keys = currentStreamKeys;
      out.stream_key_names = streamKeyNames;
      if (currentSources.length === 1) {
        out.playlist_url = currentSources[0];
      } else if (!currentSources.length) {
        out.playlist_url = "";
      } else if (!currentSources.includes(String(out.playlist_url || ""))) {
        out.playlist_url = normalizedSelected || currentSources[0];
      }
      if (currentStreamKeys.length === 1) {
        out.stream_key = currentStreamKeys[0];
      } else if (!currentStreamKeys.length) {
        out.stream_key = "";
      } else if (!currentStreamKeys.includes(String(normalizedStreamKey || ""))) {
        out.stream_key = currentStreamKeys[0];
      } else {
        out.stream_key = normalizedStreamKey;
      }
      out.framerate = Number(out.framerate || 30);
      out.update_download_cap_mbps = 50;
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
        if (id === "video_bitrate") {
          // A pause in typing is not a completed bitrate. Commit on blur/Enter.
          el.addEventListener("input", () => { bitrateEditRevision += 1; });
          el.addEventListener("blur", () => queueAutoSave(180));
          el.addEventListener("keydown", event => {
            if (event.key === "Enter" && !event.isComposing) {
              event.preventDefault();
              el.blur();
            }
          });
        } else if (el.type === "checkbox" || el.tagName === "SELECT") {
          el.addEventListener("change", () => queueAutoSave(180));
        } else {
          el.addEventListener("input", () => queueAutoSave(650));
          el.addEventListener("change", () => queueAutoSave(180));
        }
      }
      for (const id of ["twitch_enhanced_broadcasting", "twitch_enhanced_tracks_auto", "twitch_enhanced_bitrate_auto", "twitch_enhanced_max_tracks", "twitch_enhanced_max_bitrate_kbps"]) {
        const el = document.getElementById(id);
        el.addEventListener("change", () => { syncStreamSettingsLock(); queueAutoSave(180); });
      }
      if (streamKeySelect) {
        streamKeySelect.addEventListener("change", () => queueAutoSave(180));
      }
      if (streamUrlPreset) {
        streamUrlPreset.addEventListener("change", () => {
          syncStreamPlatformHint();
          queueAutoSave(180);
        });
      }
    }

    function applyTheme(themeName) {
      let theme = String(themeName || "purple").trim().toLowerCase();
      if (theme === "current") theme = "purple";
      const allowed = new Set(["blue", "purple", "red", "mono"]);
      const safeTheme = allowed.has(theme) ? theme : "purple";
      const root = document.documentElement;
      root.setAttribute("data-theme", safeTheme);
      document.body.setAttribute("data-theme", safeTheme);
      if (themeSelect) themeSelect.value = safeTheme;
    }

    function initThemeSelector() {
      if (!themeSelect) return;
      applyTheme(String(themeSelect.value || "purple"));
      themeSelect.addEventListener("change", () => {
        const selected = String(themeSelect.value || "purple").trim().toLowerCase();
        applyTheme(selected);
        queueAutoSave(180);
      });
    }

    function setupMediaUpload(prefix, endpoint, label, emptyMessage) {
      const fileInput = document.getElementById(prefix + "_file");
      const upload = document.getElementById(prefix + "_upload");
      const remove = document.getElementById(prefix + "_remove");
      const status = document.getElementById(prefix + "_status");
      const image = document.getElementById(prefix + "_preview");
      const video = document.getElementById(prefix + "_video");
      const empty = document.getElementById(prefix + "_empty");
      let busy = false;
      let configured = false;
      function setStatus(message, error = false) {
        status.textContent = message;
        status.className = "statusline " + (error ? "err" : "ok");
      }
      function buttons() {
        fileInput.disabled = busy;
        upload.disabled = busy;
        upload.textContent = busy ? "Uploading…" : "Upload media";
        upload.setAttribute("aria-busy", String(busy));
        remove.disabled = busy || !configured;
      }
      function render(media) {
        configured = !!media.configured;
        video.pause();
        image.removeAttribute("src");
        video.removeAttribute("src");
        video.load();
        image.hidden = true;
        video.hidden = true;
        empty.hidden = configured;
        empty.querySelector("strong").textContent = "No " + label.toLowerCase();
        empty.querySelector("span").textContent = emptyMessage;
        if (configured) {
          const preview = String(media.kind || "").startsWith("video/") ? video : image;
          preview.hidden = false;
          preview.src = endpoint + "?ts=" + Date.now();
        }
        buttons();
      }
      async function load() {
        try {
          const res = await fetch(endpoint + "/info", { cache: "no-store" });
          const data = await res.json();
          if (!res.ok || !data.ok) throw new Error(data.error || "Could not load media.");
          render(data.image || {});
          setStatus(configured ? label + " saved on the server." : emptyMessage);
        } catch (error) { setStatus(error.message, true); }
      }
      async function prepare(file) {
        if (file.size > 10 * 1024 * 1024) throw new Error("Choose a file no larger than 10 MiB.");
        // Preserve GIF animation and video/audio instead of flattening to a canvas.
        const extension = file.name.split(".").pop().toLowerCase();
        if (["image/gif", "video/mp4", "video/webm"].includes(file.type)
            || ["gif", "mp4", "webm"].includes(extension)) return file;
        if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)
            && !["png", "jpg", "jpeg", "webp"].includes(extension)) {
          throw new Error("Choose a PNG, JPG, WebP, GIF, MP4 or WebM file.");
        }
        const bitmap = await createImageBitmap(file);
        try {
          let scale = Math.min(1, 4096 / bitmap.width, 4096 / bitmap.height);
          for (let attempt = 0; attempt < 8; attempt++) {
            const canvas = document.createElement("canvas");
            canvas.width = Math.max(1, Math.floor(bitmap.width * scale));
            canvas.height = Math.max(1, Math.floor(bitmap.height * scale));
            const context = canvas.getContext("2d");
            if (!context) throw new Error("Could not prepare the image.");
            context.fillStyle = "#000";
            context.fillRect(0, 0, canvas.width, canvas.height);
            context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
            const blob = await new Promise((resolve, reject) => canvas.toBlob(
              value => value ? resolve(value) : reject(new Error("Could not prepare the image.")), "image/png"
            ));
            if (blob.size <= 10 * 1024 * 1024) return blob;
            scale *= 0.75;
          }
          throw new Error("Choose a smaller image.");
        } finally { bitmap.close(); }
      }
      fileInput.addEventListener("change", async () => {
        const file = fileInput.files[0];
        if (!file || busy) return;
        busy = true;
        buttons();
        setStatus("Uploading and checking media…");
        try {
          const blob = await prepare(file);
          const res = await fetch(endpoint, { method: "POST", headers: { "Content-Type": blob.type || "application/octet-stream" }, body: blob });
          const data = await res.json();
          if (!res.ok || !data.ok) throw new Error(data.error || "Media upload failed.");
          render(data.image || {});
          setStatus(label + " uploaded.");
        } catch (error) { setStatus(error.message || "Media upload failed.", true); }
        finally { fileInput.value = ""; busy = false; buttons(); }
      });
      upload.addEventListener("click", () => {
        if (busy) return;
        fileInput.value = "";
        fileInput.click();
      });
      remove.addEventListener("click", async () => {
        if (busy || !configured) return;
        busy = true;
        buttons();
        try {
          const res = await fetch(endpoint, { method: "DELETE" });
          const data = await res.json();
          if (!res.ok || !data.ok) throw new Error(data.error || "Could not remove media.");
          render(data.image || {});
          setStatus(emptyMessage);
        } catch (error) { setStatus(error.message, true); }
        finally { busy = false; buttons(); }
      });
      for (const preview of [image, video]) {
        preview.addEventListener("error", () => {
          if (!configured || !preview.getAttribute("src") || preview.hidden) return;
          preview.hidden = true;
          empty.hidden = false;
          empty.querySelector("strong").textContent = "Preview unavailable";
          empty.querySelector("span").textContent = "The saved file could not be previewed in this browser.";
          setStatus("Media saved, but the browser preview is unavailable.", true);
        });
      }
      return load;
    }
    const loadRecoveryMedia = setupMediaUpload("fallback_image", "/api/fallback-image", "Recovery media", "Recovery uses a black screen.");
    const loadTransitionMedia = setupMediaUpload("transition_media", "/api/transition-media", "Transition media", "Track changes use the recovery fallback.");

    const debugTab = document.getElementById("tab-debug");
    const debugTabButton = document.querySelector('[data-tab="tab-debug"]');
    const debugToggle = document.getElementById("debug_logging");
    const debugEmpty = document.getElementById("debug_preview_empty");
    let debugVisibilityInitialized = false;
    const debugFaultLabels = { audio_drop: "Audio loss", video_drop: "Video loss", read_error: "Read failure", source_pause: "Source stall", source_exit: "Source exit" };
    function syncDebugVisibility() {
      const enabled = debugToggle.checked;
      debugTabButton.hidden = !enabled;
      debugTab.hidden = !enabled;
      if (!enabled) {
        if (debugTab.classList.contains("active")) {
          activateTab("tab-settings");
          debugToggle.focus();
        }
        clearDebugPlayer();
      } else {
        if (!debugVisibilityInitialized) {
          try { if (localStorage.getItem(STORAGE_TAB_KEY) === "tab-debug") activateTab("tab-debug", false); } catch (error) {}
        }
        loadDebugStatus();
      }
      debugVisibilityInitialized = true;
      syncDebugButtons();
    }
    const debugStart = document.getElementById("debug_test_start");
    const debugSkip = document.getElementById("debug_test_skip");
    const debugStop = document.getElementById("debug_test_stop");
    const debugReconnect = document.getElementById("debug_preview_reconnect");
    const debugVideo = document.getElementById("debug_preview_video");
    const debugPreviewStatus = document.getElementById("debug_preview_status");
    const debugSourceStatus = document.getElementById("debug_source_status");
    const debugActionStatus = document.getElementById("debug_action_status");
    const debugPreviewLink = document.getElementById("debug_preview_link");
    const debugFaultButtons = Array.from(document.querySelectorAll("[data-debug-fault]"));
    let debugInfo = {};
    let debugPending = false;
    let debugLoading = false;
    let debugPlayer = null;
    let debugPlayerKey = "";
    let debugPlayerPreparing = "";
    let debugPlayerLibrary = null;
    let debugPlayerError = "";
    function syncDebugButtons() {
      debugStart.disabled = !debugToggle.checked || debugPending || !!debugInfo.running;
      debugSkip.disabled = !debugToggle.checked || debugPending || !debugInfo.running || !debugInfo.local_preview || !debugInfo.can_skip;
      debugStop.disabled = debugPending || !debugInfo.running || !debugInfo.local_preview;
      debugReconnect.disabled = !debugInfo.running || !debugInfo.local_preview || !debugInfo.preview_ready;
      const ready = debugToggle.checked && debugInfo.running && debugInfo.source_ready && !debugInfo.fault_pending && !debugInfo.fault_active;
      for (const button of debugFaultButtons) button.disabled = debugPending || !ready;
    }
    function clearDebugPlayer() {
      debugPlayerKey = "";
      debugPlayerPreparing = "";
      debugPlayerError = "";
      if (debugPlayer) { debugPlayer.destroy(); debugPlayer = null; }
      debugVideo.pause();
      debugVideo.removeAttribute("src");
      debugVideo.load();
      debugVideo.hidden = true;
      debugEmpty.hidden = false;
    }
    function loadPreviewPlayerLibrary() {
      if (window.Hls) return Promise.resolve();
      if (!debugPlayerLibrary) {
        debugPlayerLibrary = new Promise((resolve, reject) => {
          const script = document.createElement("script");
          script.src = "/assets/vendor/hls.light.min.js";
          script.onload = resolve;
          script.onerror = () => { debugPlayerLibrary = null; script.remove(); reject(new Error("Could not load the local preview player.")); };
          document.head.appendChild(script);
        });
      }
      return debugPlayerLibrary;
    }
    async function attachDebugPlayer(info) {
      if (!debugToggle.checked) return;
      const key = String(info.preview_id) + String(info.preview_url);
      if (debugPlayerKey === key || debugPlayerPreparing === key) return;
      clearDebugPlayer();
      debugPlayerPreparing = key;
      const url = info.preview_url + "?session=" + encodeURIComponent(info.preview_id);
      try {
        if (debugVideo.canPlayType("application/vnd.apple.mpegurl")) {
          if (debugPlayerPreparing !== key) return;
          debugVideo.src = url;
        } else {
          await loadPreviewPlayerLibrary();
          if (!debugToggle.checked || debugPlayerPreparing !== key || !debugInfo.running || !debugInfo.local_preview) return;
          if (!window.Hls || !Hls.isSupported()) throw new Error("This browser cannot play the preview. Open the playlist in an HLS-compatible player.");
          debugPlayer = new Hls({ enableWorker: false, backBufferLength: 10, liveSyncDurationCount: 2 });
          debugPlayer.on(Hls.Events.ERROR, (_event, data) => {
            if (data.fatal) {
              debugPlayerError = "Preview player interrupted. Reconnect Player retries playback without restarting the test or output process.";
              debugPreviewStatus.textContent = debugPlayerError;
            }
          });
          debugPlayer.loadSource(url);
          debugPlayer.attachMedia(debugVideo);
        }
        debugPlayerKey = key;
        debugVideo.hidden = false;
        debugEmpty.hidden = true;
        debugVideo.play().catch(() => {});
      } catch (error) { debugPlayerError = error.message; debugPreviewStatus.textContent = error.message; }
      finally { if (debugPlayerPreparing === key) debugPlayerPreparing = ""; }
    }
    async function loadDebugStatus() {
      if (debugLoading || !debugToggle.checked) return;
      debugLoading = true;
      try {
        const response = await fetch("/api/debug/state", { cache: "no-store" });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || "Could not load debug status.");
        if (!debugToggle.checked) return;
        debugInfo = payload.debug || {};
        syncDebugButtons();
        const local = debugInfo.running && debugInfo.local_preview;
        debugPreviewLink.hidden = !local;
        debugPreviewStatus.textContent = local ? (debugInfo.preview_ready ? (debugPlayerError || "Local preview running. Unmute to hear the selected source.") : "Preparing local preview...") : "Local preview is stopped.";
        document.getElementById("debug_source_pid").textContent = debugInfo.source_pid || "—";
        document.getElementById("debug_output_pid").textContent = debugInfo.output_pid || "—";
        document.getElementById("debug_recovery_count").textContent = debugInfo.recoveries || 0;
        document.getElementById("debug_last_fault").textContent = debugFaultLabels[debugInfo.last_fault] || "None";
        debugSourceStatus.textContent = debugInfo.running ? (debugInfo.source_ready ? "Source active" : "Preparing or recovering source…") : "No stream is running.";
        debugEmpty.querySelector("strong").textContent = local ? "Preparing preview…" : "Preview is stopped";
        debugEmpty.querySelector("span").textContent = local ? "Waiting for source media and preview segments." : "Select a source on the Stream tab, then start a local test.";
        if (local && debugInfo.preview_ready) await attachDebugPlayer(debugInfo);
        else if (!local && (debugPlayerKey || debugPlayerPreparing)) clearDebugPlayer();
      } catch (error) { debugActionStatus.textContent = error.message; }
      finally { debugLoading = false; }
    }
    async function debugAction(path, body = {}) {
      if (debugPending || !debugToggle.checked) return;
      debugPending = true;
      syncDebugButtons();
      debugActionStatus.textContent = "Sending debug action...";
      try {
        const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || "Debug action failed.");
        debugActionStatus.textContent = payload.transition_skip ? "Skip queued. The transition will play before the next item; the preview has a few seconds of buffering." : payload.failure ? "Failure queued: " + payload.failure.kind + ". Watch the source/output PIDs and recovery count." : "Debug action accepted.";
      } catch (error) { debugActionStatus.textContent = error.message; }
      finally {
        debugPending = false;
        await loadDebugStatus();
        await refreshState(false);
        syncDebugButtons();
      }
    }

    async function loadSettings() {
      const bitrateRevisionAtLoad = bitrateEditRevision;
      setSettingsStatus("Loading settings...", "");
      try {
        const res = await fetch("/api/settings?ts=" + Date.now(), { cache: "no-store" });
        if (!res.ok) throw new Error("load settings failed");
        const payload = await res.json();
        if (!payload.ok || !payload.settings) throw new Error(payload.error || "invalid response");
        suppressAutoSave = true;
        applySettingsToForm(payload.settings, bitrateEditRevision !== bitrateRevisionAtLoad);
        appRequestedVersion = String(payload.settings.app_update_selected_version || "");
        savedAutoInstallEnabled = !!payload.settings.auto_app_updates;
        lastSavedPayload = payloadSignature({ ...formToPayload(), video_bitrate: payload.settings.video_bitrate });
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
      // A previously queued save (including another field's) must not submit
      // the bitrate draft while its editor still has focus.
      if (document.activeElement === document.getElementById("video_bitrate")) {
        pendingAutoSave = true;
        return;
      }
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
      const bitrateRevisionAtSave = bitrateEditRevision;
      saveInFlight = true;
      setSettingsStatus(reason === "auto" ? "Saving changes..." : "Saving settings...", "");
      try {
        const res = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(currentPayload) });
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "save failed");
        suppressAutoSave = true;
        applySettingsToForm(payload.settings || {}, bitrateEditRevision !== bitrateRevisionAtSave);
        // Record the server's saved value, even if a newer draft was preserved.
        lastSavedPayload = payloadSignature({
          ...formToPayload(),
          video_bitrate: (payload.settings || {}).video_bitrate ?? currentPayload.video_bitrate
        });
        const enabledNow = !!(payload.settings || {}).auto_app_updates;
        const justEnabled = enabledNow && !savedAutoInstallEnabled;
        savedAutoInstallEnabled = enabledNow;
        setSettingsStatus(reason === "auto" ? "Changes saved automatically." : "Settings saved.", "ok");
        if (justEnabled && !appUpdateRunning) await triggerAppUpdateCheck(appRequestedVersion);
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
      if (a.running) return a.progress_message || "Checking app versions...";
      if (a.last_error) return "App update error: " + a.last_error;
      if (a.phase === "downloaded") return a.progress_message;
      const r = a.last_result;
      if (!r) return "No app update check has completed yet.";
      if (r.updater_enabled === false) return String(r.disabled_reason || "App updater disabled.");
      const current = String(r.current_version || "unknown");
      const selected = String(r.selected_version || "unknown");
      const comparison = compareVersions(current, selected);
      let state = "Up to date";
      if (comparison === 1) state = r.asset_supported ? "Downgrade available" : "Downgrade unavailable (unsupported asset)";
      else if (comparison === -1) {
        state = r.asset_supported ? "Update available" : "Update available (unsupported asset)";
      }
      let text = state + " | current " + current + " | selected " + selected;
      if (lastAppCheckAt) text += " | checked " + lastAppCheckAt;
      return text;
    }

    function updateAppVersionDropdown(info) {
      if (!info || !info.last_result) return;
      const result = info.last_result;
      const channel = String(result.channel || document.getElementById("app_update_channel").value || "release");
      const versions = Array.isArray(result.available_versions) ? result.available_versions : [];
      const selected = String(result.requested_version || "");
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
      appUpdateVersion.value = canonicalMatch(selected);
      appRequestedVersion = selected;
      appVersionsBoundToChannel = channel;
    }

    function renderAppUpdateStatus(info) {
      appUpdateRunning = !!info.running;
      const result = info.last_result || {};
      const restarted = appInstallPending && appInstallInstance &&
        String(info.instance_id || "") !== appInstallInstance;
      if (!restarted && info.restart_expected && info.operation === "install") {
        appInstallPending = true;
        appInstallInstance = String(info.instance_id || "");
      }
      if (!info.last_error && (info.phase === "installed" || restarted) && !appUpdateRefreshScheduled) {
        appUpdateRefreshScheduled = true;
        setAppUpdateStatus("Update installed. Refreshing dashboard...", "just-updated");
        setAppUpdateProgress(100, "Update installed. Refreshing dashboard...");
        setTimeout(() => window.location.reload(), 1500);
        return;
      }
      if (info.last_error || info.phase === "downloaded") appInstallPending = false;
      updateAppVersionDropdown(info);
      const comparison = compareVersions(result.current_version, result.selected_version);
      const enabled = result.updater_enabled === true;
      const supported = enabled && result.asset_supported === true;
      const updateAvailable = comparison === -1 && supported;
      const installAvailable = supported;
      const reinstallAvailable = comparison === 0 && supported;
      const disabledReason = String(result.disabled_reason || "Check the selected GitHub release first.");
      let level = "updated";
      if (info.last_error) level = "error";
      else if (info.running) level = "running";
      else if (updateAvailable) level = "outdated";
      else if (!info.last_result || !enabled) level = "unknown";
      if (info.finished_at) lastAppCheckAt = new Date(Number(info.finished_at) * 1000).toLocaleTimeString();
      setAppUpdateStatus(formatAppUpdateSummary(info), level);
      const pct = Number(info.progress_percent || 0);
      const message = String(info.progress_message || "");
      setAppUpdateProgress(pct, message ? message + " (" + pct + "%)" : "");
      checkAppUpdateBtn.disabled = !!info.running;
      checkAppUpdateBtn.title = info.running ? "An update operation is running" : "Check GitHub for the selected version";
      appUpdateVersion.disabled = !!info.running;
      document.getElementById("app_update_channel").disabled = !!info.running;
      downloadAppUpdateBtn.disabled = !!info.running || !installAvailable;
      downloadAppUpdateBtn.textContent = comparison === 1 ? "Downgrade" : comparison === 0 ? "Reinstall" : "Install";
      downloadAppUpdateBtn.title = !enabled ? disabledReason : !supported ?
        "Selected release has no self-installable asset" :
        "Install the selected version and restart, including older or identical versions";
      if (reinstallAppBtn) {
        reinstallAppBtn.disabled = !!info.running || !reinstallAvailable;
        reinstallAppBtn.title = !enabled ? disabledReason : reinstallAvailable ?
          "Reinstall the currently installed version" : "Select the installed version to reinstall it";
      }
    }

    async function loadAppUpdateStatus() {
      if (appUpdateStatusLoading) return;
      appUpdateStatusLoading = true;
      try {
        const res = await fetch("/api/app-update?ts=" + Date.now(), { cache: "no-store" });
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "failed");
        renderAppUpdateStatus(payload.app_update || {});
      } catch (err) {
        setAppUpdateStatus(appInstallPending ? "Waiting for the application to restart..." : "Failed to load app update status.",
          appInstallPending ? "running" : "error");
        // A failed check or transient request failure must never reload the page.
      } finally {
        appUpdateStatusLoading = false;
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
        renderAppUpdateStatus(payload.app_update || {});
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
        ? appRequestedVersion
        : String(selectedVersion || "").trim();
      const selectedChannel = String(document.getElementById("app_update_channel").value || "release").trim();
      appRequestedVersion = selected;
      setAppUpdateStatus("Checking app update...", "running");
      try {
        const res = await fetch("/api/app-update/check", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ selected_version: selected, channel: selectedChannel })
        });
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "failed");
        renderAppUpdateStatus(payload.app_update || {});
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
      const available = tabButtons.filter((b) => !b.hidden);
      const fallback = available.find((b) => b.classList.contains("active")) || available[0];
      const targetBtn = available.find((b) => b.dataset.tab === wanted) || fallback;
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

    const copyConsoleBtn = document.getElementById("copyConsoleBtn");
    const consoleCopyStatus = document.getElementById("consoleCopyStatus");
    let consoleCopyStatusTimer;
    copyConsoleBtn.addEventListener("click", async () => {
      const logBox = document.querySelector("#tab-console .subtab-panel.active pre");
      copyConsoleBtn.disabled = true;
      clearTimeout(consoleCopyStatusTimer);
      const ok = await copyTextToClipboard(logBox ? logBox.textContent : "");
      copyConsoleBtn.disabled = false;
      copyConsoleBtn.focus({ preventScroll: true });
      consoleCopyStatus.textContent = ok ? "Copied!" : "Unable to copy console.";
      consoleCopyStatus.className = "statusline " + (ok ? "ok" : "err");
      consoleCopyStatusTimer = setTimeout(() => { consoleCopyStatus.textContent = ""; }, 3000);
    });

    startBtn.addEventListener("click", () => api("/api/start"));
    restartBtn.addEventListener("click", restartStream);
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
    if (streamKeyAddBtn) {
      streamKeyAddBtn.addEventListener("click", () => addStreamKeyFromInput());
    }
    if (streamKeyAddInput) {
      streamKeyAddInput.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          addStreamKeyFromInput();
        }
      });
    }
    if (streamKeyAddName) {
      streamKeyAddName.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          addStreamKeyFromInput();
        }
      });
    }
    if (streamPlatformAddBtn) {
      streamPlatformAddBtn.addEventListener("click", () => addStreamPlatformFromInput());
    }
    if (streamPlatformAddInput) {
      streamPlatformAddInput.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          addStreamPlatformFromInput();
        }
      });
    }
    if (streamPlatformAddName) {
      streamPlatformAddName.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          addStreamPlatformFromInput();
        }
      });
    }
    // Fallback for stale partial DOM states: keep Add functional via delegation.
    document.addEventListener("click", (ev) => {
      const target = ev.target;
      if (!(target instanceof Element)) return;
      const addBtn = target.closest("#source_add_btn");
      if (addBtn) {
        ev.preventDefault();
        addSourceFromInput();
        return;
      }
      const addKeyBtn = target.closest("#stream_key_add_btn");
      if (addKeyBtn) {
        ev.preventDefault();
        addStreamKeyFromInput();
        return;
      }
      const addPlatformBtn = target.closest("#stream_platform_add_btn");
      if (!addPlatformBtn) return;
      ev.preventDefault();
      addStreamPlatformFromInput();
    });
    window.addEventListener("resize", () => {
      renderSourcesList();
      syncSourceSelect(sourcesState, String(sourceSelect.value || "").trim());
      renderStreamKeysList();
      syncStreamKeySelect(streamKeysState, String(streamKeySelect.value || "").trim());
      renderStreamPlatformsList();
    });
    checkAppUpdateBtn.addEventListener("click", () => triggerAppUpdateCheck());
    downloadAppUpdateBtn.addEventListener("click", () => triggerAppUpdateDownload());
    if (reinstallAppBtn) {
      reinstallAppBtn.addEventListener("click", () => triggerAppUpdateDownload(true));
    }
    appUpdateVersion.addEventListener("change", () => {
      appRequestedVersion = String(appUpdateVersion.value || "").trim();
      triggerAppUpdateCheck(appRequestedVersion);
    });
    const appUpdateChannel = document.getElementById("app_update_channel");
    if (appUpdateChannel) {
      appUpdateChannel.addEventListener("change", () => {
        appVersionsBoundToChannel = "";
        appRequestedVersion = "";
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
    debugStart.addEventListener("click", () => debugAction("/api/debug/test-stream/start", { source: String(sourceSelect.value || "").trim() }));
    debugSkip.addEventListener("click", () => debugAction("/api/debug/test-stream/skip", { source_pid: debugInfo.source_pid }));
    debugStop.addEventListener("click", () => debugAction("/api/debug/test-stream/stop"));
    debugReconnect.addEventListener("click", () => { clearDebugPlayer(); if (debugInfo.running && debugInfo.local_preview && debugInfo.preview_ready) attachDebugPlayer(debugInfo); });
    for (const button of debugFaultButtons) {
      button.addEventListener("click", () => debugAction("/api/debug/failure", { kind: button.dataset.debugFault, source_pid: debugInfo.source_pid }));
    }
    debugTabButton.addEventListener("click", loadDebugStatus);
    debugToggle.addEventListener("change", syncDebugVisibility);
    // Keep the native fields as the settings interface; render their controls
    // with the app palette rather than the operating system's menu/steppers.
    function initStyledFields() {
      let closeSelect = null;
      for (const select of document.querySelectorAll("select")) {
        const wrapper = document.createElement("div");
        wrapper.className = "styled-select";
        select.before(wrapper);
        wrapper.append(select);
        const trigger = document.createElement("button");
        trigger.type = "button";
        trigger.id = select.id + "_control";
        trigger.className = "select-trigger";
        trigger.setAttribute("role", "combobox");
        trigger.setAttribute("aria-haspopup", "listbox");
        trigger.setAttribute("aria-expanded", "false");
        const text = document.createElement("span");
        trigger.append(text);
        const menu = document.createElement("div");
        menu.id = select.id + "_options";
        menu.className = "select-menu";
        menu.setAttribute("role", "listbox");
        menu.hidden = true;
        trigger.setAttribute("aria-controls", menu.id);
        const labels = Array.from(select.labels || []);
        for (const [index, label] of labels.entries()) {
          if (!label.id) label.id = select.id + "_label_" + index;
          label.htmlFor = trigger.id;
        }
        const labelIds = labels.map(label => label.id).join(" ");
        if (labelIds) {
          trigger.setAttribute("aria-labelledby", labelIds);
          menu.setAttribute("aria-labelledby", labelIds);
        } else {
          trigger.setAttribute("aria-label", select.getAttribute("aria-label") || select.id.replaceAll("_", " "));
        }
        if (select.hasAttribute("aria-describedby")) trigger.setAttribute("aria-describedby", select.getAttribute("aria-describedby"));
        wrapper.append(trigger);
        document.body.append(menu);
        select.hidden = true;
        let active = -1;
        let search = "";
        let searchAt = 0;
        const enabled = () => Array.from(select.options).map((option, index) => ({ option, index })).filter(({ option }) => !option.disabled && !option.hidden && !option.parentElement.disabled);
        function position() {
          const rect = trigger.getBoundingClientRect();
          const roomBelow = window.innerHeight - rect.bottom - 10;
          const roomAbove = rect.top - 10;
          const above = roomBelow < 180 && roomAbove > roomBelow;
          menu.style.width = Math.min(Math.max(rect.width, 180), window.innerWidth - 16) + "px";
          menu.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8)) + "px";
          menu.style.maxHeight = Math.max(60, Math.min(280, above ? roomAbove - 6 : roomBelow - 6)) + "px";
          menu.style.top = (above ? Math.max(8, rect.top - menu.offsetHeight - 6) : rect.bottom + 6) + "px";
        }
        function highlight(index) {
          active = index;
          for (const item of menu.children) item.classList.toggle("is-active", Number(item.dataset.index) === active);
          const item = Array.from(menu.children).find(item => Number(item.dataset.index) === active);
          if (item) {
            trigger.setAttribute("aria-activedescendant", item.id);
            item.scrollIntoView({ block: "nearest" });
          } else trigger.removeAttribute("aria-activedescendant");
        }
        function close() {
          search = "";
          searchAt = 0;
          menu.hidden = true;
          trigger.setAttribute("aria-expanded", "false");
          trigger.removeAttribute("aria-activedescendant");
          if (closeSelect === close) closeSelect = null;
        }
        function sync() {
          const selected = select.selectedOptions[0];
          text.textContent = selected ? selected.textContent : "Select…";
          trigger.title = selected ? selected.textContent : "";
          trigger.disabled = select.disabled || !select.options.length;
          menu.replaceChildren();
          for (const [index, option] of Array.from(select.options).entries()) {
            if (option.hidden) continue;
            const item = document.createElement("div");
            item.id = menu.id + "_" + index;
            item.dataset.index = index;
            item.className = "select-option";
            item.setAttribute("role", "option");
            item.setAttribute("aria-selected", String(option.selected));
            item.setAttribute("aria-disabled", String(option.disabled || !!option.parentElement.disabled));
            item.textContent = option.textContent;
            item.addEventListener("pointerdown", event => event.preventDefault());
            item.addEventListener("click", () => {
              if (select.disabled || option.disabled || option.parentElement.disabled) return;
              select.selectedIndex = index;
              select.dispatchEvent(new Event("input", { bubbles: true }));
              select.dispatchEvent(new Event("change", { bubbles: true }));
              close();
              trigger.focus();
            });
            menu.append(item);
          }
          if (trigger.disabled) close();
          else if (!menu.hidden) { position(); highlight(select.selectedIndex); }
        }
        function open() {
          if (trigger.disabled) return;
          if (closeSelect) closeSelect();
          sync();
          menu.hidden = false;
          trigger.setAttribute("aria-expanded", "true");
          closeSelect = close;
          position();
          const first = enabled()[0];
          highlight(enabled().some(entry => entry.index === select.selectedIndex) ? select.selectedIndex : (first ? first.index : -1));
        }
        trigger.addEventListener("click", () => menu.hidden ? open() : close());
        trigger.addEventListener("keydown", event => {
          const entries = enabled();
          if (event.altKey && event.key === "ArrowUp") { event.preventDefault(); close(); return; }
          if (["ArrowDown", "ArrowUp", "Home", "End", "PageDown", "PageUp"].includes(event.key)) {
            event.preventDefault();
            const wasClosed = menu.hidden;
            if (wasClosed) open();
            let next = Math.max(0, entries.findIndex(entry => entry.index === active));
            if (event.key === "Home") next = 0;
            else if (event.key === "End") next = entries.length - 1;
            else if (!wasClosed) next += (event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : event.key === "PageDown" ? 5 : -5);
            if (entries.length) highlight(entries[Math.max(0, Math.min(next, entries.length - 1))].index);
          } else if (event.key === "Escape") {
            if (!menu.hidden) { event.preventDefault(); event.stopPropagation(); close(); }
          } else if (event.key === "Tab") close();
          else if (event.key === "Enter" || (event.key === " " && !search)) {
            event.preventDefault();
            if (menu.hidden) open();
            else {
              const item = Array.from(menu.children).find(item => Number(item.dataset.index) === active);
              if (item) item.click();
            }
          } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
            event.preventDefault();
            if (menu.hidden) open();
            if (Date.now() - searchAt > 700) search = "";
            searchAt = Date.now();
            search += event.key.toLowerCase();
            const match = entries.find(({ option }) => option.textContent.trim().toLowerCase().startsWith(search));
            if (match) highlight(match.index);
          }
        });
        trigger.addEventListener("blur", close);
        // Existing settings code assigns .value/.selectedIndex directly. Mirror
        // only this element's setters so programmatic updates stay visible too.
        for (const key of ["value", "selectedIndex"]) {
          const descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, key);
          Object.defineProperty(select, key, {
            configurable: true,
            get() { return descriptor.get.call(this); },
            set(value) { descriptor.set.call(this, value); sync(); }
          });
        }
        select.addEventListener("change", sync);
        new MutationObserver(sync).observe(select, { childList: true, subtree: true, attributes: true, characterData: true });
        sync();
      }
      document.addEventListener("pointerdown", event => {
        if (!event.target.closest(".styled-select, .select-menu") && closeSelect) closeSelect();
      });
      window.addEventListener("resize", () => { if (closeSelect) closeSelect(); });
      document.addEventListener("scroll", event => {
        if (!event.target.closest || !event.target.closest(".select-menu")) { if (closeSelect) closeSelect(); }
      }, true);
      for (const input of document.querySelectorAll('input[type="number"]')) {
        const wrapper = document.createElement("div");
        wrapper.className = "number-field";
        input.before(wrapper);
        wrapper.append(input);
        const controls = document.createElement("div");
        controls.className = "number-steppers";
        const label = input.labels[0]?.textContent.trim() || "value";
        const buttons = [];
        function sync() {
          const blocked = input.disabled || input.readOnly;
          for (const [button, direction] of buttons) {
            const limit = direction > 0 ? input.max : input.min;
            const atLimit = input.value !== "" && limit !== "" && (direction > 0 ? input.valueAsNumber >= Number(limit) : input.valueAsNumber <= Number(limit));
            button.disabled = blocked || atLimit;
          }
        }
        for (const direction of [1, -1]) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = direction > 0 ? "number-step step-up" : "number-step step-down";
          button.setAttribute("aria-label", (direction > 0 ? "Increase " : "Decrease ") + label);
          button.setAttribute("aria-controls", input.id);
          button.addEventListener("click", () => {
            if (input.disabled || input.readOnly) return;
            if (direction > 0) input.stepUp(); else input.stepDown();
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.dispatchEvent(new Event("change", { bubbles: true }));
            sync();
            input.focus();
          });
          buttons.push([button, direction]);
          controls.append(button);
        }
        wrapper.append(controls);
        input.addEventListener("input", sync);
        input.addEventListener("change", sync);
        const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
        Object.defineProperty(input, "value", {
          configurable: true,
          get() { return descriptor.get.call(this); },
          set(value) { descriptor.set.call(this, value); sync(); }
        });
        new MutationObserver(sync).observe(input, { attributes: true });
        sync();
      }
    }
    initStyledFields();
    bindAutoSaveListeners();
    initThemeSelector();
    rebuildStreamPresetOptions("youtube");
    syncStreamPlatformHint();
    try {
      activateTab(localStorage.getItem(STORAGE_TAB_KEY) || "", false);
      activateSubtab(localStorage.getItem(STORAGE_SUBTAB_KEY) || "", false);
    } catch (err) {
      activateTab("", false);
      activateSubtab("", false);
    }
    syncActionButtons();
    loadSettings();
    loadRecoveryMedia();
    loadTransitionMedia();
    loadDebugStatus();
    loadBinariesStatus();
    refreshState(true);
    setInterval(() => {
      refreshState(false);
      if (debugToggle.checked && (debugTab.classList.contains("active") || debugInfo.local_preview || debugPending)) loadDebugStatus();
      if (binariesUpdateRunning) loadBinariesStatus();
      if (appUpdateRunning || appInstallPending) loadAppUpdateStatus();
    }, 1200);
    setInterval(() => {
      loadBinariesStatus();
    }, 300000);
