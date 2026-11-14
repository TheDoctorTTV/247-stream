# Application updater behavior

Release and prerelease channels are filtered separately. Startup, recurring
checks, the Check button and channel changes select the highest supported
version number in that channel. The version menu lists concrete release numbers;
choosing one checks that specific release. The selected channel and concrete
version from each successful check are saved in `config.json`.

The update status compares the running version with the highest supported version
in the selected channel, independently of the installation selection. Selecting
an older release does not hide a newer available update or label an outdated
installation as up to date. A running version above the channel's highest version
is reported as newer than that channel.

## Manual installation only

Application updates are never installed automatically. Startup, scheduled and
manual checks only refresh version information. Older `auto_app_updates` values
are ignored and removed when dashboard settings are saved.

| Installed vs selected | Manual installation |
| --- | --- |
| Installed is older | Install the selected version |
| Equal | Reinstall the selected version |
| Installed is newer | Downgrade to the selected version |
| Unpublished or Cargo development build | Install a supported published release |
| Selected release lacks a supported executable asset | Installation disabled |
| GitHub check fails | Display/log the error; no installation |

Manual installation requires a concrete release in the selected channel, at or
above the minimum supported target `2.0-pre-release-2`. The button changes to
Install, Reinstall or Downgrade based on that selection. The server revalidates
the release and refuses installation while a stream or local preview is active.
Installing replaces the running executable and restarts the application.

## Checks and status

The app always checks once at startup. `app_update_check_minutes` controls later
checks; `0` (Never) disables recurring checks without suppressing the startup
check. Dashboard refreshes and status GETs do not start app update operations.
The legacy `check_updates_startup` preference supplies the interval only when no
explicit interval is saved: hourly when enabled, Never when disabled.

The selected channel and last checked version survive restarts. Startup and
recurring checks discover the highest version again; saved versions do not pin
these checks to an older release. There is no separate Latest menu choice or
saved follow-latest mode.

Jobs expose operation, phase, outcome and `restart_expected` independently of
their running flag. Completed checks never reload the dashboard. The page reloads
only after installation is confirmed or a new application instance is observed
following an installation. A boot identifier changes even for an exec restart
retaining the same process ID. Network failures during checks do not trigger
refreshes.

App and tool downloads have no application-imposed speed cap. Downloads remain
staged and checked for completeness, executable permissions are set, and app
installation uses the existing replacement/systemd restart handoff. FFmpeg and
yt-dlp provisioning are separate from application updates.

Check results, installed/newest/selected versions, installation requests and
failures use the normal application logger, including `logs/app.log` when app
file logging is enabled.

## FFmpeg provisioning

Automatic FFmpeg acquisition and binary version checks share the stable-release
asset discovery in `ffmpeg_release`. It reads the public
[FFmpeg-Builds release metadata](https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/tag/latest),
selects the highest eligible release branch at version 8 or newer, and downloads
the non-shared GPL tar.xz for Linux x86_64 or aarch64. Master/nightly branch builds,
LGPL builds lacking libx264, and shared-library variants are excluded.

Both stream-start provisioning and the manual binary updater enforce the minimum.
Cached versions below 8 are not accepted as healthy. Downloaded executables must
report a supported version at least as new as the selected release branch and
pass the existing startup/audio-video encoding probe before replacing the local
binary. Failed downloads/probes preserve the previous executable. A PATH fallback
must satisfy the same minimum and startup probe; an old FFmpeg is never accepted
to satisfy an upgrade. A forced FFmpeg refresh failure is reported even when a
compatible previous version remains available.

The provider documents glibc 2.28+ and Linux 4.18+ for these builds; see its
[build requirements](https://github.com/FFmpeg-Builds/FFmpeg-Builds#targets-variants-and-addins).
These version and encoding probes run automatically during tool provisioning.
