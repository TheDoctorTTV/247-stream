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

Once the output has started with source media, playlist/URL resolution, replacement
source startup and recovery use a paced CPU-encoded recovery image (or a black
picture when unset) plus stereo silent AAC to feed the same bridge. CPU filler avoids
another NVENC/QSV/VAAPI encoding session while a replacement source prepares.
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

The bridge uses `-copyts -start_at_zero`: it removes the initial program offset
once and preserves subsequent feeder timing. It no longer uses the 0.5-second
FFmpeg discontinuity correction that caused alternating audio/video corrections.
This applies to ordinary output, local preview and Twitch Enhanced Broadcasting.
Debug logs record the producer PID and shared clock offset at each handoff;
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

## Recovery image

Settings → Recovery Image supports upload, preview, replacement and removal.
Upload image opens the file picker and uploads the selected image directly.
PNG/JPG/WebP images up to 20 MiB must have an exact 16:9 aspect ratio;
1920 × 1080 pixels is recommended, not required. The browser normalizes them to a
still RGB/RGBA PNG with dimensions at most 4096 pixels and stored size at most
8 MiB, preserving 16:9 during any downscaling. The server also checks the ratio
on new uploads, along with PNG structure, chunk checksums, dimensions and
size, then atomically replaces its managed `fallback-image.png` in the app data
directory. Client filenames and paths are not used. The asset is persistent
across restarts, independent of the Remember playlist/key setting, and excluded
from source control and release packaging.

FFmpeg loops the image at the program frame rate, scales it to fit the output
without cropping, and pads unused space with black. Audio remains silent. With
no configured image, fallback is black. An active fallback checks for image
changes once per second and replaces only its image producer; the RTMP bridge
and EB encoders remain running. Decoder failure of the image producer switches
to black until the stored image changes. Upload validation failures preserve the
previous image. Ordinary app logs record upload dimensions/removal and the type
of fallback, without image contents or original filenames.

The [Debug tab](DEBUG_TESTING.md) provides a local preview of the selected source and explicit
source-only fault injection for manual recovery verification without broadcasting.
