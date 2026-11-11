# Debug tab and local recovery testing

The Debug tab previews the source selected on the Stream page in the browser
and exposes explicit source-only failure controls. These are tools for the user
to run; no tests or streams were started by the agent.

The Debug tab is visible only while Settings → Debug logging is enabled. Turning
it off hides the tab immediately, stops Debug status polling and detaches browser
preview playback. It does not stop an active local test or broadcast; use the
ordinary Stream controls to stop it. A saved Debug tab selection is ignored while
debugging is disabled.

## Local preview

Start Local Test uses the source currently selected on the Stream page, including
YouTube playlist/video, Twitch-source retrieval or direct HLS. It uses the selected
resolution, frame rate, bitrate and encoder, and the same playlist, persistent
feeder, source watchdog, fallback image and recovery code as normal streaming.
It produces one H.264/AAC HLS output in the app data directory `debug-preview/` and serves it through the existing dashboard HTTP
server. The selected source is fetched normally; only the output is local.
No streaming destination is opened, EB negotiation is skipped, and no destination
stream key is required. A selected source is required. Saved streaming settings
are not changed. Local mode never automatically interrupts an existing stream.

Initial startup waits for the selected source's audio and video before creating
the preview output; the first picture is source media, not the recovery image.
The preview playlist may return 404 while preparation is in progress. Fallback
is used only for interruptions or transitions after source playback has started.

The player is muted initially; unmute it to hear the selected source. HLS adds
several seconds of playback buffering. Reconnect Player restarts only browser playback,
not the producer or output process. An external HLS player can use the playlist
URL. Browser playback uses the locally bundled HLS.js v1.7.4 lightweight player
when native HLS is unavailable; no CDN is contacted. Its notices and license
are available at `/assets/vendor/hls-LICENSE.txt`.

Local preview exercises the real selected-source retrieval, playlist transitions,
HTTP reconnect configuration and URL-refresh/recovery paths, using one output
encoder. It does not test destination network delivery, Twitch's EB configuration
API or multitrack EB output. Those require destination-specific runtime verification.
The preview and recovery code are independent of the selected destination. Normal
streaming supports configured RTMP/RTMPS destinations, including custom platforms;
only Enhanced Broadcasting negotiation is restricted to Twitch endpoints.

The rolling preview keeps six two-second playlist segments plus a small deletion
window, rather than a complete recording. A new test removes only the previous
managed playlist/segment files. The directory is excluded from packaging and
source control. Stop Local Test stops only a local preview; it cannot stop a
normal broadcast. The ordinary Stop Stream button also stops an active preview.

## Failure controls

Controls also work on a normal stream using persistent output, so button labels
explicitly indicate source disruption. They work independently of Debug logging.
Only one fault can be pending or active per source generation. Requests are
bound to a source PID; stale commands are discarded at source handoff. No route
accepts an arbitrary process ID or targets the output process.

| Kind | Behavior | Expected recovery |
| --- | --- | --- |
| `audio_drop` | Drops source MPEG-TS audio PID 257 and freezes its watchdog heartbeat | Eight-second audio stall watchdog switches to fallback and reopens source |
| `video_drop` | Drops source MPEG-TS video PID 256 and freezes its watchdog heartbeat | Eight-second video stall watchdog switches to fallback and reopens source |
| `read_error` | Sets the same terminal-input-failure signal used for FFmpeg demux/read errors | Source recovery starts promptly |
| `source_pause` | SIGSTOP pauses only the owned source process | Stalled progress triggers recovery; cleanup SIGCONT resumes it before requesting quit |
| `source_exit` | SIGKILL terminates only the owned source process | Process-exit recovery starts promptly |

Packet drops simulate missing source media and watchdog telemetry; they do not
close an upstream HTTP connection. Simulate Read Error does not manufacture a
real TLS/CDN error. These controls validate controller recovery paths without
claiming to verify every network failure mode.

The Debug tab displays current source/output PIDs, recovery count, readiness and
the last injected fault. For a successful source recovery, the source PID should
change, recovery count increase, fallback image/black output appear during the
gap, and the original output PID remain unchanged. With a real selected source,
the source picture and audio should resume after fallback.

For timestamp handoffs, enable debug logging and check for `Playout clock handoff`
entries with a single shared A/V offset for each new producer. After repeated
fallback/source and playlist transitions, check that there is no recurring
alternation of positive/negative `timestamp discontinuity` warnings, no
`Non-monotonic DTS` warnings, and that audible sync stays stable. Include image
to black fallback changes, which also create a new producer. Source content PTS
can restart on recovery; the bridge program PTS should continue advancing.
These are manual checks for the user's build; they have not been run by the agent.

## HTTP endpoints

All URLs are relative to the existing dashboard origin.

| Method | Endpoint | Request/response |
| --- | --- | --- |
| GET | `/api/debug/state` | Running/local-preview flags, readiness, source/output PIDs, recovery count, preview URL and fault state |
| POST | `/api/debug/test-stream/start` | `{"source":"<selected-source-url>"}` (or `{}` to use the saved selected source); refuses when another stream is running; returns local preview playlist URL |
| POST | `/api/debug/test-stream/stop` | `{}`; stops only a local preview |
| POST | `/api/debug/failure` | `{"kind":"audio_drop","source_pid":12345}`; PID is an optional stale-source guard, never a target chosen by the client |
| GET | `/api/debug/preview/index.m3u8` | Rolling local preview playlist, 404 while not ready |
| GET | `/api/debug/preview/segment-<number>.ts` | Only managed segment filenames are served |

Suggested manual sequence: select a source, start local preview, unmute, record the output PID,
inject one failure, wait for recovery and compare PIDs/count, repeat for other
fault types, then stop the local test. Upload/remove a recovery image to check
image-to-black fallback behavior as well. No build, test execution, browser
session or destination stream has been run by the agent for this change.

Custom destinations can supply a complete RTMP/RTMPS URL without a separate
stream-key field when their server permits it. EB eligibility is based on Twitch
hostnames; a custom/shared ingest hostname is not assumed to be Twitch.
