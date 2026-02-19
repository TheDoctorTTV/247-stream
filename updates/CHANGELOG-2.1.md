# Stream247 v2.1 Changelog

## Summary
v2.1 focuses on maintainability and deployment simplicity by splitting the monolithic runtime into modular packages and formalizing Stream247 as a terminal-first Linux server runtime.

## Added
- New modular package layout under `stream247/`:
  - `constants.py`
  - `config.py`
  - `models.py`
  - `utils.py`
  - `updates.py`
  - `web.py`
  - `worker.py`
  - `runtime.py`
  - `qtcompat.py` (headless signal shim)
- Package entrypoints:
  - `stream247/__init__.py`
  - `stream247/__main__.py`
- Backward-compatibility re-export module:
  - `stream247/shared.py`

## Changed
- Legacy top-level `Stream247.py` is now a thin compatibility launcher to the modular runtime entrypoint.
- Core runtime responsibilities are now separated by domain:
  - config normalization and persistence
  - update/version logic
  - stream worker lifecycle
  - local web dashboard/runtime control
- Build flow is aligned with the modular architecture while preserving current Linux terminal binary behavior.

## Removed
- Monolithic single-file application structure as the primary implementation model.
- AppImage-specific runtime directory behavior.
- Desktop-GUI framework dependency path (PySide/Qt import fallback chain); replaced by an internal headless signal shim.
- Desktop-target self-install update artifacts from supported in-app install targets (e.g. `.appimage`, `.exe`).

## Fixed
- PyInstaller entrypoint regression from package-relative imports (`__main__.py` execution context); build/runtime now use stable compatibility entry flow again.
- Browser-cookie lock warning text generalized for terminal/server use (not desktop-browser-brand specific wording).

## Notes
- This release is intentionally terminal-first for Linux server/headless deployment.
- Existing launch workflows remain compatible (`python3 Stream247.py` and built `stream247-server` binary).
