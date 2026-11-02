# App Deck backend

## Operations and providers

Primary verb: `launch`. Reads installed desktop entries from absolute `$XDG_DATA_HOME/applications` (default `$HOME/.local/share/applications`) followed by `$XDG_DATA_DIRS/applications` (default `/usr/local/share:/usr/share`). XDG precedence applies, including user Hidden tombstones suppressing lower-priority entries. Empty or relative `XDG_DATA_HOME` falls back to the default user directory; empty `XDG_DATA_DIRS` uses the default system directories. Nested entries have standard relative-path desktop IDs with separators replaced by `-`. No persistent app index or daemon is introduced.

Rows show actual Name, canonical desktop ID, available GenericName/Comment/Categories and Terminal status. Application type, Hidden/NoDisplay, OnlyShowIn/NotShowIn and TryExec are honored; current desktop defaults to Hyprland only when XDG_CURRENT_DESKTOP is absent. Malformed/duplicate-key/unsupported entries are skipped. Installed user desktop entries are trusted in exactly the normal launcher sense: GIO may execute their installed Exec/DBus action. This backend never parses or evaluates Exec and does not pretend to sandbox the launched application.

A fresh visible installed ID resolves to its known desktop-file path; `gio launch <absolute-known-desktop-file>` does the platform interpretation. Action membership uses `app_rows(with_previews=False)`: only optional icon discovery is skipped; the same full bounded XDG scan, precedence and visibility checks still run. Normal catalogues retain previews. No arbitrary file argument, URI or command is accepted from the UI. Successful GIO handoff is reported as accepted, application startup not confirmed. Missing GIO fails rather than installing anything.

## Bounds and limitations

At most eight absolute XDG roots, 4,096 directory entries per root, depth three without following directory symlinks, 4,096 admitted apps; each desktop file is regular and <=64 KiB. IDs <=199 characters, display names <=160, details <=500. Up to 1,024 rows and an 800,000-byte row budget are emitted with an honest limited message; actions revalidate full scanned membership. Files can change between refresh and activation and are re-read before dispatch; normal same-user installed-file trust still applies.

Local absolute raster Icon files and bounded hicolor PNG/JPEG/pixmaps lookups are supported. Previews are absolute local paths with raster headers, <=8 MiB, dimensions <=4096 and paths <=512 characters. SVG, remote images, arbitrary icon-theme inheritance and localized Name keys are intentionally not implemented. Raster header checks are not complete image decoding; UI decoding may reject a corrupt local file. No icons are bundled or downloaded. Unknown desktop schemas and apps beyond the bounded scan are not launchable through this plugin.

## CLI and safety contract

Run with `/usr/bin/python3 runtime/0.2.1/backend.py catalog`, or `action --id <exact-known-id> --verb <primary-verb> --apply`. The catalogue is read-only. Runtime/data paths derive from `__file__`, never cwd. One bounded JSON object is emitted; errors are JSON with nonzero exit and no traceback/provider output. Unknown IDs/verbs, missing `--apply`, unavailable providers, nonzero helper exits and deadlines fail closed. Selection is checked against fresh full bounded membership, not the displayed list. **No restore operation is supported or advertised**: copying and launching do not have a meaningful reversible restore.

Actions use literal argument vectors, no shell/eval, no execution of commands from displayed metadata. Helpers are found on normal PATH (tests replace it with a private shim directory); their stdin/stdout/stderr are discarded. GNU `/usr/bin/timeout --signal=KILL 3` supplies an independent process-group deadline even if the QML-owned backend is killed; a five-second Python wait is backup. An already-successful app launch/clipboard ownership can intentionally outlive its short helper. Cancelling after dispatch cannot revoke an accepted action. A timeout does not prove that the request had no effect.

Success means a completed helper exited zero and accepted the request. It is **not proof of actual app/game startup or live clipboard ownership**. The latter require an explicitly authorized native session test. No clients, fonts, packages or dependencies are installed; no bindings, themes, configs, services or live IPC are changed by this implementation task.

## Tests and receipts

From this project's root, use the shipped offline runner with TMPDIR naming an existing approved private scratch directory:

```sh
TMPDIR="${TMPDIR:?Set TMPDIR to an existing private scratch directory}" env -u PYTHONPATH PATH=/usr/bin /usr/bin/python3 scripts/run-tests.py --backend-only
```

The shipped runner invokes `/usr/bin/python3 -m unittest discover -s tests -p 'test_*.py' -v` inside bwrap, with `--unshare-all --clearenv`, read-only `/usr` and source, fake HOME, private proc/dev/tmp/run, no network, no live HOME, display, Hyprland or session bus. It masks production theme/background/shell setters and streams results without creating receipts. No package installation or GUI is involved. The isolation test verifies loopback-only networking, an unreachable external test address, absent live paths/sockets and cleared session variables.

The private `release-output/run-backend-tests.py` runner and ignored `release-output/` receipts below are historical development evidence, not shipped files or prerequisites for the public test command.

Feature-specific RED/GREEN receipts are immutable under ignored `release-output/`; earlier failed runs are retained, not overwritten. Tests run real parsers and CLI entry points against synthetic local files/SQLite and literal executable shims. They cover selected metadata, helper acknowledgement and observable argv logs, malformed/oversized/nonregular inputs, missing clients, revalidation, unknown selections and deadline failure. The cancellation test checks an actual TERM-ignoring descendant becomes non-running after its backend is terminated. Shims establish the adapter boundary only, not genuine client integration. `release-output/backend-result.json` records counted methods, final receipt and exact source hashes. UI/package/README integration remains parent-owned.

Original backend receipt: `final-backend-GREEN.txt`: **15 test methods passed, zero failures/errors, no warnings**.

Post-review XDG correction: `parent-xdg-red.log` reproduced three assertions; `parent-xdg-green.log` passes **17 methods**, including empty/relative XDG root fallback and Hidden override preservation. Current portable runner: `env -u PYTHONPATH /usr/bin/python3 scripts/run-tests.py --backend-only`. Original receipts/hashes are retained as historical checkpoints, not hashes of the corrected source.

Artwork-work correction: `release-output/ponytail-fixes-1793557673318497712/{RED.log,GREEN.log,completion.json}` records strict RED→GREEN with **18 methods passing** in the portable offline runner. A Python audit hook counts actual raster-file open attempts (including missing files) in fresh backend subprocesses, without replacing filesystem reads. The mixed named/absolute/missing-icon fixture retains 53 catalogue artwork opens and both valid previews; each of seven valid/rejected actions drops to zero artwork opens. Literal shim argv remains unchanged; vanished entries, fresh Hidden overrides and unknown IDs still fail closed. These are operation counts, not measured latency or real application startup.
