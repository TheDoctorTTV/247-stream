# Stream247 v2.1 Pre-release 8 Changelog

## Added
- Stream Keys tab in the web dashboard for managing multiple saved stream keys.
- Stream key name support in settings payload/config (`stream_key_names`) for friendly labels.
- Stream Platforms tab in the web dashboard for managing saved custom RTMP platform services.

## Changed
- Stream settings now use a stream key dropdown that mirrors source selection behavior.
- When exactly one stream key is configured, it is selected automatically.
- When two or more stream keys are configured, the selected key can be chosen from a dropdown.
- Stream Platform selector now includes built-in presets plus saved custom platforms.
- Custom platform add/remove controls moved out of Stream settings into the dedicated Stream Platforms tab.

## Fixed
- Preserved backward compatibility for existing single-key configs by auto-populating `stream_keys` from `stream_key`.
- Preserved backward compatibility for existing custom `rtmp_base` values by auto-importing unknown URLs into saved Stream Platforms.
