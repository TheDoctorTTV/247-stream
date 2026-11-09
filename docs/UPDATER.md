# Application updater behavior

The application compares its embedded Cargo package version to the selected
GitHub release tag. Release and prerelease channels are filtered separately;
Latest chooses the highest supported version in that channel. An explicit
selection must exist in that channel. The channel and requested version are
saved in config.json; an empty requested version follows Latest.

| Installed vs selected | Automatic installation enabled | Disabled |
| --- | --- | --- |
| Installed is older | Install a supported executable asset and restart | Enable Install Update |
| Equal | No update | No update; explicit same-version Reinstall remains available |
| Installed is newer | No downgrade | No downgrade; Install Update is disabled |
| Newer release lacks a supported asset | Explain the limitation; no install | Explain the limitation; no install |
| GitHub check fails | Log and display the error; no restart | Log and display the error; no restart |

Startup checks respect check_updates_startup. A successful check, including a
manual or dashboard periodic check, installs a newer target when auto_app_updates
is enabled. Cargo development binaries retain the existing manual-download-only
policy. A status GET never starts an application update operation.

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
