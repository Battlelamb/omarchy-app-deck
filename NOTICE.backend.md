# Backend source and asset notice

The backend and tests are original GPL-3.0-only code implementing narrowly ported behavior after source review. No HyDE script is executed or vendored at runtime. Each backend is independent and uses Python standard-library modules and existing system CLI tools only.

## Reviewed HyDE lineage

The v0.3.0 favorites/pinned groups/category filtering and installed desktop-action snapshots are original GPL-3.0-only additions. Actions are read from the user's installed desktop entries and interpreted by GIO; no application Exec scripts, desktop files, icons or new donor code are bundled. The harmless real-GIO file-writing fixture is original test code.

Repository: https://github.com/HyDE-Project/HyDE

Pinned commit: `fa2fd57afc7203b1b5653d3271f3a57ea3873f97`. Upstream repository license is GNU GPL version 3; this does not determine individual dataset/font/image rights.

- `Configs/.local/lib/hyde/rofiselect.sh` — SHA-256 `fb586ae833464700c79c7153b0cac04f72060450c99a01dc3d950199b9cf6b45`

The donor selector is a Rofi style chooser, not an app database. Its optional grid-presentation idea is the only inherited behavior; style setters, notifications, shell initialization and eval are excluded. Application indexing here follows the freedesktop XDG desktop-entry layout; installed GIO performs the actual desktop-file interpretation. No parsed Exec text is evaluated by this backend. No Omarchy stock UI, application icons, desktop entries or launcher data are copied into the package.

Tests create synthetic desktop entries, manifests and SQLite databases in fake HOME. The tiny one-pixel raster fixture is test data, not a real application/game asset. Backend action receipts prove literal argv and helper acknowledgement through private executable shims, not live clipboard ownership or actual app/game startup.
