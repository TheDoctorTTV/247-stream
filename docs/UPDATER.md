# Application updater behavior

The application compares its embedded Cargo package version to the selected
GitHub release tag. Release and prerelease channels are filtered separately.
Startup, recurring checks, the Check button and channel changes select the
highest supported version number in that channel. The version menu lists concrete
release numbers; choosing one checks that specific release. There is no separate
Latest choice or saved follow-latest mode.

The selected channel and concrete version from each successful check are saved
in config.json. Saved versions record the last selection; startup and recurring
checks discover the highest version again. Manual installation requires a concrete
version from the menu and revalidates that release on the server.

Before allowing automatic installation, the updater verifies that the running
version has an exact matching published release tag, ignoring a leading `v` or
`V`. This lookup includes both release channels and paginates the GitHub release
list; draft releases do not count. Unpublished versions can still check for
updates and manually install a supported published release. A failed GitHub
lookup never authorizes installation.

| Installed vs selected | Automatic checks | Manual installation |
| --- | --- | --- |
| Installed is older | Install when automatic installation is enabled | Install the selected version |
| Equal | No installation | Reinstall the selected version |
| Installed is newer | No installation | Downgrade to the selected version |
| Running version has no published tag | Check only; no automatic installation | Install the selected version |
| Cargo development build | Check only; no automatic installation | Install the selected version |
| Selected release lacks a supported executable asset | No installation | Explain the limitation; disabled |
| GitHub check fails | Display/log the error; no installation | Display/log the error; no installation |

Manual installation supports the existing minimum target `2.0-pre-release-2` and
requires the selected version to exist in the selected channel. The Install
button changes its label to Downgrade or Reinstall as appropriate. Manual requests
recheck eligibility on the server; disabling dashboard
buttons is not the only enforcement.

The app always checks once at startup. After that, app_update_check_minutes
controls recurring checks on an app timer; 0 (Never) disables recurring checks
without suppressing the startup check. Dashboard refreshes do not start checks.
The legacy check_updates_startup preference only supplies the recurring interval
when no explicit interval is saved: hourly when enabled, Never when disabled.
A successful check, including a startup, manual, or timed check, installs only a
newer target when auto_app_updates is enabled. Auto install is disabled by default
on a fresh install; when disabled, checks never install updates automatically.
Reinstalling or downgrading always requires an
explicit install request. The selected channel and last checked version remain
saved across the restart; the startup check refreshes the highest available
version. Cargo executables run from a target directory allow manual installation
but never automatic installation, even if Auto install was previously enabled.
Manual installation replaces the running executable and restarts the app.
Tool updates (FFmpeg/yt-dlp) are
unaffected. A status GET never starts an application update operation.

Release matching establishes automatic-install eligibility, not binary provenance: a
locally compiled executable copied outside Cargo's target directory with the
same version as a published release passes the automatic-install version gate.

Jobs expose operation, phase, outcome and restart_expected independently of their
running flag. Completed checks never reload the dashboard. The page reloads only
when installation is confirmed or a new application instance is observed after
an installation was in progress. A boot identifier changes even for an exec
restart retaining the same process ID. Network failures during checks do not
trigger refreshes. Downloads remain staged, executable permissions are set, and
installation uses the existing replacement/systemd restart handoff.

Check starts/results, compared versions, the selected channel, installation mode,
and operation failures are written through the normal application logger. They
are included in logs/app.log when application file logging is enabled.

App and tool downloads have no application-imposed speed cap. Downloads are still
staged and checked for completeness before installation.

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
