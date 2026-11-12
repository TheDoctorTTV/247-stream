# Source recovery and persistent playout

The November 10 failure was an early EOF/read error on YouTube's separate Opus
input. Video continued, so waiting only for the source process to exit left the
broadcast permanently without audio. Recovery now supervises media progress as
well as process lifetime.

## Input retries

HTTP(S) connection options are repeated before every input, including separate
audio. Reads time out after five seconds. FFmpeg can reconnect on premature
connection loss, network-connect errors, HTTP 429 and 5xx, with three retries,
a maximum individual reconnect delay of five seconds and a total delay limit of
15 seconds; the progress watchdog can intervene earlier when media is not advancing. A normal finite-file EOF is not configured to reconnect endlessly.
These are FFmpeg 8+ options; automatic tool provisioning already requires 8+.

Terminal input demux/read errors trigger source recovery. An eight-second pause
in either audio or video packet production also triggers recovery. These counters
are maintained even with debug and file logging disabled. A replacement must
produce both audio and video within 30 seconds. A complete source transport
stall is detected even when per-track stats are unavailable. Builds lacking
FFmpeg's packet-stat options log that audio-only stall detection is unavailable.

## Single feeder and fallback

Source FFmpeg and fallback FFmpeg each have a private Unix socket. One Rust
controller forwards whole MPEG-TS packets to the persistent output's media
socket. Their bytes cannot interleave. Each producer has at most 4 MiB of pending
media; no full video is downloaded or stored for recovery.

Initial startup resolves the selected source and buffers actual audio/video
before opening the output bridge. No recovery image or black/silent fallback
starts the broadcast or local preview. If the initial source fails, the existing
retry/playlist logic runs with the output closed until a source is ready.

Once the output has started with source media, interruptions use paced CPU-encoded
recovery media (or a black picture when unset) plus stereo silent AAC to feed the
same bridge. Normal playlist handoffs use transition media when configured, as
described below. CPU filler avoids another NVENC/QSV/VAAPI encoding session while
a replacement source prepares.
When replacement A/V packets are ready, the controller finishes and drains the
filler before switching to the prepared source. A detected failure may produce
buffering before fallback takes over; this does not promise a zero-gap handoff.

Source and filler cleanup drains their private pipes while waiting for FFmpeg's
quit/flush path, with a deadline and a logged source-only forced-stop fallback.
Stop cancels recovery, stops the producer/filler, closes the bridge's media
socket, and uses the existing graceful output-drain/unpublish path.

The bridge keeps its RTMP session and EB encoder configuration through source
recovery. For ordinary persistent output, it now encodes H.264 using the selected
encoder rather than copying source H.264. This keeps output encoder settings
stable across source/filler changes, and adds an output encoding stage in that
mode. EB already had this stage. It can increase CPU/GPU use for non-EB output.

## Resume and timing

For YouTube clips with known finite duration, the controller refreshes signed
URLs through yt-dlp and seeks both inputs to the last common audio/video content
position, with a quarter-second overlap. It does not seek based only on the video
clock after audio has stopped.

A single Rust feeder owns the bridge's transport clock. Before switching, it
waits for actual first audio and video PES timestamps in the replacement's socket
buffer. After the outgoing filler has drained, it calculates one shared offset
that places both tracks after their last forwarded presentation/decode times.
That offset stays fixed for the producer's lifetime and is applied equally to
PTS, DTS and PCR/OPCR. The feeder also continues transport continuity counters
across producers and handles the 33-bit timestamp wrap. Source/fallback encoders
use no B-frames and one AAC frame per PES to bound handoff delay and account for
the final frame's duration.

All private MPEG-TS producers (source, transition and recovery) explicitly use
`-max_delay 0 -muxdelay 0 -muxpreload 0`. The buffer-mode `max_delay` applies
only to direct output. FFmpeg's MPEG-TS muxer uses `max_delay` to offset PTS/DTS
relative to PCR. Mixing a seven-second source delay with zero-delay filler made
the shared clock insert approximately seven seconds at source handoffs to keep
PCR monotonic. Matching producer delays removes that mismatch while retaining
one shared A/V offset and monotonic PCR. Input probing/retry settings are unchanged.

The bridge uses `-copyts -start_at_zero`: it removes the initial program offset
once and preserves subsequent feeder timing. Ordinary persistent output and local
preview also use `-fps_mode:v passthrough`, without an output `-r` or `-fpsmax`
(which FFmpeg rejects in combination with passthrough): source/filler producers
already normalize frame cadence, so the bridge must not run a second CFR synchronizer
that can duplicate the last transition frame at a handoff. Enhanced Broadcasting
retains its negotiated per-rendition frame-rate filters. It no longer uses the 0.5-second
FFmpeg discontinuity correction that caused alternating audio/video corrections.
This applies to ordinary output, local preview and Twitch Enhanced Broadcasting.
Debug logs record the producer PID, shared clock offset and incoming audio/video
gaps relative to the previous track ends at each handoff. Gaps above 100 ms also
produce an ordinary warning, preserving the source's A/V relationship rather
than concealing it with separate audio/video timestamp resets;
source watchdog checkpoints remain in the original content timeline.

This preserves the producer's A/V relationship rather than independently moving
each track. It does not repair already-desynchronized source media or guarantee
that a failed input will never cause buffering before the watchdog intervenes.

A recovery can repeat content between the last common checkpoint and failure
detection. Seeking accuracy also depends on the source/container. Clips without
finite duration metadata reopen from the beginning, with a warning. Live sources
reopen at their live position. Known audio-less clips receive a synthetic silent
audio input; this does not classify quiet scenes as failures.

A source that exits more than three seconds before its expected end is treated
as incomplete when per-track stats are available. Normal clip completion still
advances the playlist. After three failed recovery attempts, a playlist item is
skipped while the bridge/fallback remains available for the next item. Live
relays retain fallback and repeat bounded recovery rounds after a 30-second
cooldown until stopped. Actual output/encoder failures are reported separately;
source recovery cannot preserve a connection that has itself failed.

Recovery is enabled on persistent-output paths. Explicitly disabling persistent
YouTube output retains its direct-output behavior; per-input HTTP retry options
still apply there. Debug logs include common playback checkpoints and per-track
progress; ordinary app logs include fallback activation, recovery reasons,
handoffs and exhausted retries. Existing credential sanitization applies.

## References and validation

The per-input retry approach follows [pipr's input setup](https://github.com/m60/pipr/blob/master/pipr).
The fallback/normalization approach draws on [ffplayout's documented playout features](https://github.com/ffplayout/ffplayout#features).
Neither project is added as a separate service or dependency.

Protocol behavior: [FFmpeg 8 HTTP reconnect options](https://github.com/FFmpeg/FFmpeg/blob/n8.0/doc/protocols.texi)
and [timestamp-discontinuity correction](https://github.com/FFmpeg/FFmpeg/blob/n8.0/doc/ffmpeg.texi).

No builds, tests, tool executions or live streams were run by the agent. Source
formatting and manual review do not verify compilation, seek behavior, handoff
quality or compatibility with the actual Twitch ingest. The user's beta build
and runtime verification remain necessary.

## Recovery and transition media

Settings has separate Recovery media and Transition media uploads, previews,
replacement and removal. Both accept PNG/JPG/WebP images, GIFs, MP4 and WebM
videos up to 10 MiB. Still images are normalized to PNG by the browser; GIFs and
videos retain animation and video audio. Media may use any aspect ratio and is
scaled to fit with black padding, without cropping. 1920 × 1080 is recommended;
the server accepts dimensions up to 4096 pixels per side. Animated WebP/APNG are
not offered as animations; use GIF or video for animated assets.

Runtime upload validation checks the file signature and asks the installed
FFmpeg to decode its first video frame, with a 20-second deadline and local-only
protocols. Videos must report a finite duration. Invalid uploads preserve the
previous asset. This is initial validation rather than a full-file integrity
check; later decoder failures fall back during playout. FFmpeg must be installed
before uploading. The server commits a manifest pointing at an immutable file
in the data directory; client filenames never choose filesystem paths. Active
playout holds a private hard link, so replacing or removing an upload cannot
invalidate the current decoder. The links are removed when their producer is
released. Assets persist independently of the Remember setting, and are excluded
from source control and release packaging. Legacy `fallback-image.png` uploads
continue to work until replaced or removed.

Recovery media loops with silent audio during failures, URL resolution and
replacement startup. With no recovery media, fallback is black. An active
recovery checks for replacements once per second. Decoder failure switches to
black until that recovery asset changes. Neither fallback nor transitions open
the initial broadcast; actual source A/V must be ready first.

On persistent playlist output, completing a source starts the transition before
next-item resolution. Images and GIFs have a five-second minimum. Video plays
at least once, including its audio when present, for its reported container duration.
A video without audio receives silence. The next item resolves while the
transition plays. After that duration, its decoder buffers A/V before handoff;
this keeps the next track from encoding far ahead while waiting its turn.
Transition timing requires both elapsed wall time and the full media duration
forwarded for audio and video. When preparation takes longer, videos loop with
their audio, while images remain visible and GIFs keep animating. Once the first
full playthrough has finished, handoff occurs as soon as the next source is ready,
including partway through a subsequent loop. This
avoids flashing recovery media during an ordinary slow handoff. Replacements of
transition media apply at the next transition. The playlist wrap also transitions.
The ordinary Skip bypasses the remaining transition; Debug → Skip to transition
ends a local test item with a transition. Source failures use recovery media.
Transition decoder failure cancels the transition and uses recovery fallback.
With no transition configured, the existing recovery fallback fills track changes.

The transition uses the same single MPEG-TS feeder and shared A/V transport clock,
without restarting RTMP or Enhanced Broadcasting encoders. It applies to local
preview as well. Live relays have no between-item transitions. Explicitly disabled
persistent YouTube output retains direct-output behavior and cannot insert these
transitions.

No builds, tests, media commands or live streams were run for these changes.
Compilation, upload handling, timing and live ingest behavior remain for the
user to verify.

The [Debug tab](DEBUG_TESTING.md) provides a local preview of the selected source and explicit
source-only fault injection for manual recovery verification without broadcasting.

## November 11 transition-freeze investigation

The user's initial local-test logs in `releases/logs/` showed a transition
producer continuing to generate frames until handoff. The bridge then repeated
417 frames at 60 fps. Disabling the ordinary bridge's second CFR synchronizer
removed those generated repeats, but did not remove the underlying timestamp gap.
The first attempt also combined incompatible `-r 60` and passthrough flags;
removing the bridge's output `-r` corrected that startup failure.

The subsequent handoff diagnostics established the timing mismatch: the
23:03:42.644 source handoff had video/audio gaps of 6.950/7.376 seconds;
the 23:04:09.641 recovery handoff had gaps of 6.967/7.376 seconds. The source
command included buffer-mode `-max_delay 7000000` alongside `-muxdelay 0`, while
transition/recovery used zero mux delay. FFmpeg's MPEG-TS muxer uses the effective
`max_delay` for its PTS/DTS offset and PCR lead. PCR continuity therefore forced
the incoming source's presentation times ahead of the previous producer.

The source now selects `-max_delay 0` for private MPEG-TS, and transition/recovery
set it explicitly to zero as well. Direct output retains the configured delay.
Handoff-gap logging remains in place for the user's next run. These corrections
have not been built or runtime-verified by the agent.

Owning FFmpeg sources: [MPEG-TS timestamp and PCR delay](https://github.com/FFmpeg/FFmpeg/blob/n8.0/libavformat/mpegtsenc.c)
and [video sync options](https://ffmpeg.org/ffmpeg.html#Advanced-options).
