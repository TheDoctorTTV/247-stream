# Application updater behavior

The application compares its embedded Cargo package version to the selected
GitHub release tag. Release and prerelease channels are filtered separately;
Latest chooses the highest supported version in that channel. An explicit
selection must exist in that channel. The channel and requested version are
saved in config.json; an empty requested version follows Latest.

Before allowing installation, the updater verifies that the running version has
an exact matching published release tag, ignoring a leading `v` or `V`. This
lookup includes both release channels and paginates the GitHub release list;
draft releases do not count. A version absent from published releases disables
manual and automatic app installation with an explanation in the dashboard and
app log. Check remains available so publishing the version can enable updating
without restarting the app. A failed GitHub lookup never authorizes installation.

| Installed vs selected | Automatic checks | Manual installation |
| --- | --- | --- |
| Installed is older | Install when automatic installation is enabled | Install the selected version |
| Equal | No installation | Reinstall the selected version |
| Installed is newer | No installation | Downgrade to the selected version |
| Running version has no published tag | Disabled | Disabled |
| Selected release lacks a supported executable asset | No installation | Explain the limitation; disabled |
| GitHub check fails | Display/log the error; no installation | Display/log the error; no installation |

Manual installation supports the existing minimum target `2.0-pre-release-2` and
requires the selected version to exist in the selected channel. The Install
button changes its label to Downgrade or Reinstall as appropriate. The separate
Reinstall button is available when the selected version matches the installed
version. Manual requests recheck eligibility on the server; disabling dashboard
buttons is not the only enforcement.

Startup checks respect check_updates_startup. A successful check, including a
manual or dashboard periodic check, installs only a newer target when
auto_app_updates is enabled. Reinstalling or downgrading always requires an
explicit install request. The selected channel/version remains saved across the
restart. Cargo executables run from a target directory have app updating disabled
even if their version matches a published tag. Tool updates (FFmpeg/yt-dlp) are
unaffected. A status GET never starts an application update operation.

Release matching establishes version eligibility, not binary provenance: a
locally compiled executable copied outside Cargo's target directory with the
same version as a published release passes the version gate.

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

This change has been formatted and manually reviewed only. No builds, runtime
checks, or tests were run by the agent.
