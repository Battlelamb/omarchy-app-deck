# App Deck backend · v0.4.1

## Exact CLI

From the checkout root:

```sh
/usr/bin/python3 runtime/0.4.1/backend.py catalog
/usr/bin/python3 runtime/0.4.1/backend.py preferences
/usr/bin/python3 runtime/0.4.1/backend.py preferences --apply --payload '{"options":{"category":"Office","view":"all"}}'
/usr/bin/python3 runtime/0.4.1/backend.py preferences --apply --payload '{"collections":{"Writing":["org.example.Editor.desktop"]},"options":{"collection":"Writing"}}'
/usr/bin/python3 runtime/0.4.1/backend.py preferences --apply --payload '{"reset":true}'
/usr/bin/python3 runtime/0.4.1/backend.py action --id org.example.Editor.desktop --verb launch --apply
/usr/bin/python3 runtime/0.4.1/backend.py action --id org.example.Editor.desktop::New --verb launch --apply
/usr/bin/python3 runtime/0.4.1/backend.py action --id org.example.Editor.desktop --verb copy-app-id --apply
/usr/bin/python3 runtime/0.4.1/backend.py action --id org.example.Editor.desktop --verb favorite --apply
```

Example IDs are illustrative: launch/favorite require IDs from fresh local membership. These are all commands/verbs: launch is primary; copy-app-id and favorite are secondary. Declared desktop actions use `launch`, not a new command verb or an input command string. No arbitrary Exec/path/URI/output argument, action payload, shell runner or Restore. Every action/preference write requires `--apply`; reads create no files. Invalid arguments/fields/IDs/verbs fail nonzero with one bounded JSON object and no traceback/provider-output dump. Runtime paths derive from `__file__`, not cwd.

Catalogue retains rows/actions/actionLabel/message and adds preferences/settingsSchema. Optional row metadata includes primary `category`, `favorite:true`, personal `collection` and validated local preview. Favorite toggles validate fresh full membership and do not launch. Successful launch/ID-copy requests update Recent only after a zero helper exit; a later storage error preserves acceptance with a warning. Acceptance is not application startup confirmation.

## Preferences, pinned groups and categories

| Option | Type/default | Values |
|---|---|---|
| `view` | enum, `all` | `all`, `favorites`, `recent` |
| `collection` | string, empty | Exact pinned-group name <=256 characters; empty = all |
| `category` | string, empty | Empty or `[A-Za-z][A-Za-z0-9-]{0,63}` desktop category token |
| `density` | enum, `comfortable` | `comfortable`, `compact` |
| `layout` | enum, `grid` | `grid`, `list` |
| `sortOrder` | enum, `default` | `default`, `name`, `favorites-first` |

The three display enums are saved for the native view to interpret; they do not alter source membership or action dispatch. Old valid stores missing these keys receive the defaults on read without an automatic rewrite. Existing options, favorites, Recent and collections remain intact; Reset includes the new defaults.

Category is validated before settings storage and again before use. It matches any declared valid category, not only the row's first category. Views, personal groups and category filters intersect before display limiting; Recent retains most-recent-first order. Unknown group names yield an empty selection. Filters never replace fresh full membership checks at action time. Collections are the pinned groups; there is no separate group file or daemon.

Preference patches accept only options/favorites/collections. Options merge; supplied favorites or collections replace the complete section. Reset is exactly sole-key `{"reset":true}`. Recent/version cannot be patched. JSON object payloads are <=16,384 UTF-8 bytes; duplicate keys, nonfinite numbers, unknown fields/options, invalid types and controls fail. Sibling preferences.py provides read/change/reset/toggle/remember, bounded owned/private regular-file reads, no symlink following, locked atomic replacement and explicit corruption errors. Reset does not overwrite corrupt files silently or alter desktop/application settings.

Storage is absolute `$XDG_CONFIG_HOME/omarchy/lego/app-deck/preferences.json`, otherwise `$HOME/.config/omarchy/lego/app-deck/preferences.json`. Bounds: 512 favorites, 32 recents, 64 named groups, 512 IDs/group, nonempty control-free IDs <=256 characters. Stored IDs are inert data until fresh visible installed membership validates them. Reads do not create directories.

New preference writes are preflighted under the store lock against an aggregate 128 KiB compact UTF-8 document budget before replacement. Individual list/text maxima do not imply their Cartesian maximum is supported. Legacy valid stores remain bounded to 16 MiB for reads and explicit Reset/recovery; a rejected write leaves the existing file unchanged. Replies use the common UTF-8 encoder below 1 MiB (including the newline, at most 1 MiB); the UI action parser uses the same strict byte ceiling. Oversized optional preference snapshots on successful commands are omitted with a warning, preserving `ok:true`; actual catalogue overflow still fails closed, without truncating saved data. A Recent-save failure likewise warns without reversing confirmed helper/export acceptance.

## Copying the installed desktop-entry ID

`copy-app-id` requires explicit `--apply`, no payload, and exact membership in a fresh full bounded XDG scan with previews disabled. The fixed helper vector is `wl-copy --type text/plain;charset=utf-8 -- DESKTOP_ID`; no path, Exec, URI, command or caller clipboard text is accepted. A declared-action row must still be listed and valid, then copies its parent `.desktop` ID without `::Action`. Filters and display caps do not authorize or invalidate an otherwise admitted entry. No GIO launch or temporary action snapshot is used for copying. It writes only the requested literal text, never reads clipboard/history, and remembers the selected row ID only after helper acceptance. A later preference error/oversized optional snapshot warns without undoing or falsifying acceptance; helper acceptance is not proof of live clipboard ownership.

## XDG scan and desktop-action execution

Roots: absolute XDG_DATA_HOME/applications (default `~/.local/share/applications`) then XDG_DATA_DIRS/applications (default `/usr/local/share:/usr/share`). Empty/relative data-home falls back to the user default; empty data-dirs uses system defaults. XDG precedence and Hidden tombstones suppress lower-priority duplicates. Nested file paths map to standard hyphenated desktop IDs. Application type, name, Hidden/NoDisplay, OnlyShowIn/NotShowIn and TryExec are checked; current desktop defaults to Hyprland only when its environment value is absent. Malformed/duplicate-key/oversized/nonregular entries are skipped.

Base application rows launch with `gio launch <fresh-known-installed-path>`. Installed desktop entries retain ordinary launcher trust: GIO may execute their installed Exec or DBus behavior. The backend never parses Exec into a shell command, and does not sandbox the resulting app. No supplied string is treated as a command/path to launch.

Declared desktop actions appear as separate `App · Action` rows with IDs `desktop-id::Action`. Only the first 32 unique tokens in the installed Actions list are considered; tokens match `[A-Za-z0-9_-]{1,48}` and require a matching Desktop Action section, nonempty plain Name and nonempty control-free Exec <=4096 UTF-8 bytes. Unlisted/missing/removed actions are rejected; actions share base visibility and category checks. Their known rows consume the same 4096-row full membership cap as apps.

GIO's CLI has no desktop-action switch. For an admitted action the backend creates a private temporary directory and 0600 desktop snapshot containing Type, the action's Name/Exec and base Icon/Path/Terminal/TryExec/StartupNotify. It invokes GIO with that snapshot, waits for helper acknowledgement and removes it in all ordinary exits. GIO interprets field codes and installed Exec syntax; the backend does not split/eval it. `%k` names the temporary snapshot and `%c` its action name. Actions depending on the original desktop identity or extra vendor fields are not claimed compatible; installed-file changes can still race ordinary base-entry dispatch. A caller killed mid-snapshot may leave a private temporary directory; no persistent index, installed desktop-file rewrite or arbitrary snapshot path is accepted.

## Bounds, icons and lifecycle

At most eight roots, 4096 scanned entries/root, depth three without directory-symlink recursion, and 4096 admitted app/action rows. Desktop files <=64 KiB, base IDs <=199 characters, action IDs <=249, labels <=160 and detail <=500. Display <=1024 rows within an 800,000-byte row budget, with an honest limit message; action membership uses the fresh complete bounded scan with previews disabled. No artwork opens occur on action authorization paths.

Local absolute PNG/JPEG Icon files and bounded hicolor/pixmaps lookups are supported: header checks, <=8 MiB, dimensions <=4096, preview path <=512. No SVG/remote icons, arbitrary theme inheritance, localized names or complete raster decoding. Corrupt local images can fail UI decoding. No icons or desktop entries are bundled/downloaded. Complete JSON stays below 1 MiB; optional snapshot overflow never changes confirmed launch status or truncates stored data.

Literal helpers run on normal PATH with discarded stdin/stdout/stderr. GNU `/usr/bin/timeout --signal=KILL 3` provides an independent process-group deadline even after backend termination; a five-second wait is backup. Already-accepted app requests cannot be revoked by dismissing. Normal apps intentionally outlive the short helper; a timeout does not establish that nothing launched.

## Offline verification

```sh
TMPDIR="${TMPDIR:?Set TMPDIR to an existing private scratch directory}" env -u PYTHONPATH PATH=/usr/bin /usr/bin/python3 scripts/run-tests.py --backend-only
```

The shipped bwrap runner clears environment, uses fake HOME/private run/tmp and no live display/bus/compositor sockets/network, masks production setters and runs unittest discovery. Tests cover real XDG/config parsing, override/visibility/cap regressions, no-artwork membership, declared-action revalidation, preferences and source compilation. An actual installed GIO launches only a harmless file-writing fixture inside the offline box, proving desktop-snapshot interpretation without windows. Other fixtures use literal argv shims; neither proves every real application works.

Earlier release-output/v030-result.json describes v0.3.0 only. Current tests add literal base/action ID copying, fresh visibility/action refusal, caller-path/payload rejection, no-artwork full membership, helper failure/timeout and truthful preference warnings, plus saved-display-schema validation/migration. Native UI integration, live clipboard/apps, upgrade/rollback, hosted CI and publication need separate gates.
