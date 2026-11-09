# Logs and debug diagnostics

Settings apply immediately. App log (`log_to_file`) and Save FFmpeg log
(`ffmpeg_log_to_file`) independently control file saving. Debug logging
(`debug_logging`) controls extra detail in the app console, journal and whichever
file sinks are enabled; it does not implicitly enable file saving.

## Layout and storage

Inside the application data directory:

- `logs/app.log`: application events, update checks/results, worker lifecycle and debug summaries.
- `logs/ffmpeg.log`: FFmpeg commands, warnings/errors and throttled progress, tagged by role and PID.
- `logs/app-<timestamp>-<pid>.log.gz` and `logs/ffmpeg-<timestamp>-<pid>.log.gz`: closed archives.

Active files remain plain text. They rotate when a new application/stream session
starts, file saving is re-enabled, or their size reaches 4 MiB for the app log and
16 MiB for FFmpeg. Each family retains its newest 32 closed archives, removing
older ones. FFmpeg progress is throttled to approximately one line per second per
process; warnings and errors are not dropped by that throttle.

Closed files are compressed on a bounded background queue with gzip level 1
(fast compression), using the Rust compression backend. Streaming threads do not
compress files. A compressed file is finalized before the plain archive is
removed. Compression failures preserve the original file; a full queue leaves
plain archives for a later logging startup to retry. Interrupted compression
retries from the retained plain file. Retention also covers plain archives, so
persistent compression failures do not cause unbounded archive growth. Fixed,
non-sensitive warnings go to stderr if compression or file writes fail.

New log directories use mode 0700 and new log files/archives use mode 0600.
Legacy `latest.log` and `latest-*.log` files are left untouched; new logs are not
mixed with that older history. The release archive's explicit file list excludes
runtime logs and configuration.

## Debug information

- App version, distro, kernel, architecture, CPU model and logical CPU count.
- GPU vendor/device IDs, DRM card, kernel driver and exposed module version.
- FFmpeg and yt-dlp versions and executable locations.
- App/system uptime, process resident memory and available/total host memory,
  approximately every 30 seconds.
- Source category (YouTube playlist/video, Twitch, direct HLS), resolved transport,
  separate audio input, selected yt-dlp format and requested output settings.
- FFmpeg's actual input and output codec descriptions, including stream properties
  such as frame rate, sample rate and layout when FFmpeg reports them.
- FFmpeg process role/PID, finish/cancel reasons, and periodic per-track mux packet
  counts, last PTS, timestamp gap and time since each track advanced.

Per-track measurement is capability-detected with FFmpeg's help output. Supported
FFmpeg processes emit numeric packet records over stderr. The app consumes them
into counters; the per-packet records are never saved to logs. This lightweight
collection stays active when debug logging is off so enabling the toggle can
report current progress without restarting the stream. Codec headers are cached
in memory and shown in debug summaries. Unsupported builds continue without the
extra stats and report their unavailability.

A mux timestamp gap is diagnostic evidence, not a measurement of audible lip-sync:
encoder lookahead/reordering and buffering can affect the sampled difference.
Packet activity also cannot prove that the encoded audio is audible. Actual
internal FFmpeg queue occupancy is not exposed here; the configured buffer mode
is logged rather than inventing a measured queue level. No corrective timing or
streaming changes are performed by diagnostics.

## Sensitive information

All application logging passes through one sanitizer before reaching the console,
web history, or either file sink. It removes saved/current stream keys (including
previously used keys retained in memory), credential assignments, authentication
headers and Bearer/Basic credentials. URLs retain scheme, host and any explicit port (including default ports); credentials,
paths, queries and fragments are removed. Home directories are abbreviated.
Raw configuration, environments, cookies, request headers and arbitrary FFmpeg
source metadata are not dumped. Debug mode never disables sanitization.

No builds, runtime executions, compression trials or tests were run by the agent
for this change. Source formatting/manual review do not verify runtime behavior.

FFmpeg packet summaries include each output stream index separately. With Twitch Enhanced Broadcasting this identifies each video rendition; the summary mux PTS gap uses the lowest-index video and audio streams and is not a measurement of lip sync.
