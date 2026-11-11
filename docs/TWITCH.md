# Twitch output and Enhanced Broadcasting

## Stopping a stream

Stop requests FFmpeg's `q` shutdown through a dedicated command pipe. With the persistent RTMP bridge, the source finishes first; the app then closes the bridge's media pipe and lets the output consume EOF. This allows FFmpeg to flush encoders/muxers and execute its RTMP unpublish/delete-stream close path. Skip finishes the current source but leaves the bridge connected.

The source has 15 seconds to finish. The bridge has 15 seconds to drain, then receives SIGINT with another 5-second cleanup deadline. Only a hung process reaches the existing terminate/kill fallback, which is logged. Network failures or a forcibly killed application can still interrupt the connection. Twitch's disconnect-protection display needs verification on the actual ingest; a graceful local exit cannot guarantee Twitch's response.

## Dashboard controls

Select a Twitch output on the Stream page to expose **Twitch Enhanced Broadcasting** (off by default). Enabling it exposes:

- **Maximum Video Tracks:** Auto, or a cap of 1–6 video renditions.
- **Maximum Streaming Bandwidth (kbps):** Auto, or a cap for aggregate upload bitrate.

Auto sends an unset limit to Twitch's GoLive configuration endpoint. Twitch returns encoder configurations, rendition dimensions/frame rates/bitrates, audio settings, and an ingest endpoint. Manual values are maximums; Twitch can return fewer tracks or lower bitrates. Twitch may omit the optional response `status` field or return it as null; this proceeds to full configuration validation, as in OBS. An explicit error or unsupported status still stops startup. Negotiation happens at stream startup. These settings cannot be edited during a stream and apply on the next start.

Your server encodes and uploads each rendition; Auto does not move the encoding work to Twitch. The existing resolution/frame-rate controls describe the source canvas, and the ordinary video bitrate still controls the intermediate source encode. The enhanced bandwidth control applies to the negotiated output. All negotiated renditions and one AAC audio track are muxed into one persistent RTMP/RTMPS connection across playlist transitions.

The configuration request includes the stream key and host CPU, memory, GPU and OS capabilities, as OBS's protocol requires. Raw requests/responses are never logged. Returned ingest credentials and configuration IDs are registered with the log sanitizer before use. Debug logging reports per-output-track packet/PTS progress, codec information and negotiated rendition summaries without those secrets.

## Requirements and current scope

This implementation is experimental and has not been built or tested against Twitch yet. It identifies itself as Stream247, rather than impersonating OBS. Acceptance of this client and the returned configurations must be established with the user's beta testing.

- Upstream **FFmpeg 8 or newer** is required for enhanced FLV multitrack video. Tool provisioning and the binary updater now select stable FFmpeg 8+ non-shared GPL builds from [FFmpeg-Builds](https://github.com/FFmpeg-Builds/FFmpeg-Builds). A cached FFmpeg 7 or older is upgraded on the next stream start. The staged binary must pass version and startup/encoding probes before installation; an incompatible download leaves the previous file intact and prevents starting with an unsupported version. PATH fallbacks must also meet the minimum version.
- Supports 1–6 H.264 SDR BT.709 limited-range 4:2:0 renditions and one live AAC audio track. Encoder families translate to x264, NVENC, QSV, VAAPI or AMF where available in FFmpeg. Supported rate control, bitrate, keyframe interval, profile, B-frame count and x264/NVENC presets are translated explicitly. Scaling currently uses bicubic filtering.
- Requires an audio stream in the intermediate media input. HDR, HEVC, AV1, additional canvases, a separate VOD audio track, and OBS dynamic bitrate control are not implemented.
- Unsupported configurations and unavailable encoders fail explicitly. There is no silent fallback to ordinary broadcasting. If an established enhanced output dies, the worker stops rather than repeatedly reconnecting with stale negotiation data.
- Encoder availability checks at stream startup do not establish that the server can sustain several concurrent encoding sessions. This implementation retains the existing source encode before encoding the output ladder, adding CPU/GPU work and a generation of video compression.

Source retry, per-track stall recovery and a single feeder with black-video/silence fallback now preserve the existing EB bridge through source replacement. See [source recovery](SOURCE_RECOVERY.md) for timing, resource costs and unverified runtime behavior. This has not yet established that every long-duration audio/timestamp failure is fixed.

## Source references

The negotiation follows the public protocol and flow used in [OBS GoLive request construction](https://github.com/obsproject/obs-studio/blob/master/frontend/utility/GoLiveAPI_PostData.cpp), [GoLive data models](https://github.com/obsproject/obs-studio/blob/master/frontend/utility/models/multitrack-video.hpp), and [OBS multitrack output](https://github.com/obsproject/obs-studio/blob/master/frontend/utility/MultitrackVideoOutput.cpp). It is an explicit Rust/FFmpeg implementation, not an embedded OBS runtime.

Transport references: [FFmpeg 8 FLV muxer](https://github.com/FFmpeg/FFmpeg/blob/n8.0/libavformat/flvenc.c), [FFmpeg RTMP close path](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/rtmpproto.c), and [OBS RTMP output](https://github.com/obsproject/obs-studio/blob/master/plugins/obs-outputs/rtmp-stream.c).
