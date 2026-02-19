# Stream247 v2.1 Pre-release 5 Changelog

## Summary
This pre-release focuses on app-update version matching reliability when release tag wording varies.

## Added
- Canonical version-suffix normalization in app update logic so equivalent labels (for example `pre-release` and `prerelease`) resolve to the same target.
- Token-aware suffix comparison for pre-release labels to improve ordering consistency.

## Changed
- App update release selection now matches selected versions using normalized version strings instead of exact raw tag text.
- Dashboard-side version comparison and version dropdown selection now use canonical matching to avoid false update availability.

## Fixed
- Fixed false "Install available" state when current and selected versions were equivalent but formatted with slightly different wording.
- Fixed selected-version mismatch behavior caused by exact-string comparison of release tags.
