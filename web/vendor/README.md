# HLS.js browser preview player

Version: v1.7.4, lightweight UMD bundle.

Source: https://github.com/video-dev/hls.js/releases/tag/v1.7.4

Bundle SHA-256: 482cccb3a224554f64d8c2cf0d335dba4a3819bdb726afbfddfe15de4c277d82

License: Apache-2.0; see Apache-2.0.txt and the upstream notices in hls-LICENSE.txt.

Bundled locally for the Debug tab; playback makes no CDN requests.

# Tabler Icons

Version: v3.53.0, outline and filled SVGs.

Source: https://tabler.io/icons (npm package `@tabler/icons`)

License: MIT; see tabler-icons-LICENSE.txt.

Only the icons the dashboard uses are bundled, as the inline sprite
`tabler-icons.svg`. To add icons, list them in `make-icon-sprite.py` and run:
`python3 web/vendor/make-icon-sprite.py PATH/TO/node_modules/@tabler/icons`
