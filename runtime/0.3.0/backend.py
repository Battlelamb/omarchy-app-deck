#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Read known XDG desktop entries; delegate execution to the installed GIO."""
from itertools import islice
from pathlib import Path
import argparse
import configparser
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preferences

SLUG = 'app-deck'
PREFERENCE_SCHEMA = {
    'view': {'type':'enum', 'default':'all', 'label':'Show', 'choices':['all','favorites','recent']},
    'collection': {'type':'string', 'default':'', 'label':'Pinned group (empty = all)', 'maxLength':256},
    'category': {'type':'string', 'default':'', 'label':'Desktop category (empty = all)', 'maxLength':64},
}


def preference_metadata(rows=None):
    prefs = preferences.read(SLUG, PREFERENCE_SCHEMA)
    if rows is not None:
        view, collection = prefs['options']['view'], prefs['options']['collection']
        if view != 'all':
            members = prefs[view if view == 'favorites' else 'recent']
            rows[:] = [row for row in rows if row['id'] in members]
            if view == 'recent':
                rows.sort(key=lambda row: members.index(row['id']))
        if collection:
            rows[:] = [row for row in rows if row['id'] in prefs['collections'].get(collection, [])]
            for row in rows:
                row['collection'] = collection
    for row in rows or []:
        if row['id'] in prefs['favorites']:
            row['favorite'] = True
    return {'preferences': prefs, 'settingsSchema': PREFERENCE_SCHEMA}


def data_roots():
    home = Path.home()
    first = Path(os.environ.get('XDG_DATA_HOME') or str(home / '.local/share'))
    if not first.is_absolute():
        first = home / '.local/share'
    directories = os.environ.get('XDG_DATA_DIRS') or '/usr/local/share:/usr/share'
    roots = [first] + [Path(s) for s in directories.split(':') if s]
    return list(dict.fromkeys(p for p in roots if p.is_absolute()))


def bounded_read(path, limit=65536):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Not a regular file.')
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('File exceeds the supported byte limit.')
    return raw


def desktop_paths(folder):
    pending, budget = [(folder, 0)], 4096
    while pending and budget:
        parent, depth = pending.pop(0)
        try:
            with os.scandir(parent) as entries:
                batch = sorted(islice(entries, budget), key=lambda x: x.name)
        except OSError:
            continue
        budget -= len(batch)
        for entry in batch:
            if entry.is_dir(follow_symlinks=False) and depth < 3:
                pending.append((Path(entry.path), depth + 1))
            elif entry.name.endswith('.desktop'):
                yield Path(entry.path)


def plain(value, limit=160):
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return ''
    return value.strip()[:limit]


def local_image(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 24 <= info.st_size <= 8388608:
                return None
            raw = stream.read(65536)
        width = height = 0
        if raw.startswith(b'\x89PNG\r\n\x1a\n') and raw[12:16] == b'IHDR':
            width, height = int.from_bytes(raw[16:20]), int.from_bytes(raw[20:24])
        elif raw.startswith(b'\xff\xd8'):
            pos = 2
            while pos + 8 < len(raw) and raw[pos] == 255:
                marker = raw[pos+1]
                if marker == 255:
                    pos += 1
                    continue
                length = int.from_bytes(raw[pos+2:pos+4])
                if length < 2:
                    break
                if marker in (192,193,194):
                    height, width = int.from_bytes(raw[pos+5:pos+7]), int.from_bytes(raw[pos+7:pos+9])
                    break
                pos += length + 2
        resolved = str(path.resolve())
        if 0 < width <= 4096 and 0 < height <= 4096 and len(resolved) <= 512:
            return resolved
    except (OSError, ValueError):
        return None
    return None


def app_icon(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    path = Path(value)
    if path.is_absolute():
        return local_image(path)
    if not re.fullmatch(r'[A-Za-z0-9_.+-]{1,128}', value) or value in ('.','..'):
        return None
    for root in data_roots()[:8]:
        for size in ('256x256','128x128','64x64','48x48','32x32','16x16'):
            for ext in ('.png','.jpg','.jpeg'):
                image = local_image(root/'icons/hicolor'/size/'apps'/(value+ext))
                if image:
                    return image
        for ext in ('.png','.jpg','.jpeg'):
            image = local_image(root/'pixmaps'/(value+ext))
            if image:
                return image
    return None


def validate_category(value):
    if not isinstance(value, str) or (value and not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,63}', value)):
        raise ValueError('Category must be a desktop category token or empty.')
    return value


def app_rows(with_previews=True, category=''):
    rows, paths, seen = [], {}, set()
    category_matches = set()
    def result():
        selected = [row for row in rows if not category or row['id'] in category_matches]
        return selected, {row['id']: paths[row['id']] for row in selected}
    desktop = set(os.environ.get('XDG_CURRENT_DESKTOP', 'Hyprland').split(':'))
    for root in data_roots()[:8]:
        folder = root / 'applications'
        for path in desktop_paths(folder):
            ident = '-'.join(path.relative_to(folder).parts)
            if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.+-]{0,190}\.desktop', ident) or ident in seen:
                continue
            seen.add(ident)
            parser = configparser.ConfigParser(interpolation=None, strict=True)
            parser.optionxform = str
            try:
                parser.read_string(bounded_read(path).decode('utf-8'))
                if parser.defaults():
                    raise ValueError('Desktop entries do not support inherited DEFAULT fields.')
                entry = parser['Desktop Entry']
            except (OSError, UnicodeError, ValueError, configparser.Error, KeyError):
                continue
            name = plain(entry.get('Name', ''))
            if entry.get('Type') != 'Application' or not name:
                continue
            if any(entry.get(k, 'false').lower() == 'true' for k in ('Hidden', 'NoDisplay')):
                continue
            only = set(filter(None, entry.get('OnlyShowIn', '').split(';')))
            excluded = set(filter(None, entry.get('NotShowIn', '').split(';')))
            if (only and not desktop.intersection(only)) or desktop.intersection(excluded):
                continue
            if entry.get('TryExec') and not shutil.which(entry['TryExec']):
                continue
            if not entry.get('Exec') and entry.get('DBusActivatable', 'false').lower() != 'true':
                continue
            detail = ' · '.join(s for s in [ident, plain(entry.get('GenericName', '')), plain(entry.get('Comment', '')), plain(entry.get('Categories', ''))] if s)[:500]
            if entry.get('Terminal', 'false').lower() == 'true':
                detail = detail[:480] + ' · Terminal'
            row = {'id': ident, 'label': name, 'detail': detail}
            categories = [c for c in entry.get('Categories', '').split(';') if re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,63}', c)]
            matches_category = category in categories
            if matches_category:
                category_matches.add(ident)
            if categories:
                row['category'] = categories[0]
            image = app_icon(entry.get('Icon', '')) if with_previews else None
            if image:
                row['preview'] = image
            rows.append(row)
            paths[ident] = path
            for action in list(dict.fromkeys(entry.get('Actions', '').split(';')))[:32]:
                if len(rows) >= 4096:
                    break
                section = 'Desktop Action ' + action
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', action) or section not in parser:
                    continue
                item = parser[section]
                title, command = plain(item.get('Name')), item.get('Exec', '')
                if not title or not command or len(command.encode('utf-8')) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in command):
                    continue
                action_id = ident + '::' + action
                if matches_category:
                    category_matches.add(action_id)
                action_row = dict(row, id=action_id, label=(name + ' · ' + title)[:160])
                rows.append(action_row)
                # GIO interprets the installed action Exec; never parse it as a shell command.
                action_entry = configparser.ConfigParser(interpolation=None)
                action_entry.optionxform = str
                action_entry['Desktop Entry'] = {'Type':'Application', 'Name':title, 'Exec':command}
                for key in ('Icon', 'Path', 'Terminal', 'TryExec', 'StartupNotify'):
                    if key in entry:
                        action_entry['Desktop Entry'][key] = entry[key]
                paths[action_id] = action_entry
                if len(rows) >= 4096:
                    break
            if len(rows) >= 4096:
                return result()
    return result()


def displayed_rows(rows):
    out, size = [], 2
    for row in rows:
        amount = len(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) + 1
        if len(out) >= 1024 or size + amount > 800000:
            break
        size += amount
        out.append(row)
    return out


def catalog():
    options = preferences.read(SLUG, PREFERENCE_SCHEMA)['options']
    rows, _ = app_rows(category=validate_category(options['category']))
    metadata = preference_metadata(rows)
    shown = displayed_rows(rows)
    limited = ' Display limited; full scanned membership remains validated for actions.' if len(shown) < len(rows) else ''
    return {**metadata, 'rows': shown, 'message': f'{len(rows)} installed desktop applications (bounded XDG scan).'+limited, 'actions':['launch','favorite'], 'actionLabel':'Launch'}


def run_helper(argv):
    executable = shutil.which(argv[0])
    if not executable:
        raise ValueError(f'{argv[0]} is unavailable; install or configure it separately.')
    # A separate deadline survives cancellation/destruction of the QML-owned backend.
    with subprocess.Popen(['/usr/bin/timeout', '--signal=KILL', '3', executable, *argv[1:]], stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          start_new_session=True) as proc:
        try:
            code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise ValueError('Helper timed out; request completion is unconfirmed.') from None
    if code in (124, -signal.SIGKILL):
        raise ValueError('Helper timed out; request completion is unconfirmed.')
    if code != 0:
        raise ValueError(f'Helper rejected the request (exit {code}); check the client separately.')


def payload_object(raw):
    if len(raw.encode('utf-8')) > 16384:
        raise ValueError('Payload exceeds 16384 UTF-8 bytes.')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate payload key.')
            result[key] = value
        return result
    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('Nonfinite payload number.')
        return result
    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_float=number,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite payload number.')))
    except (json.JSONDecodeError, RecursionError):
        raise ValueError('Payload must be a bounded JSON object.') from None
    if not isinstance(result, dict):
        raise ValueError('Payload must be a JSON object.')
    return result


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('Invalid arguments; use catalog or action --id ID --verb launch --apply.')


def main():
    try:
        parser = Parser(allow_abbrev=False, add_help=False)
        parser.add_argument('command', choices=['catalog', 'action', 'preferences'])
        parser.add_argument('--id')
        parser.add_argument('--verb')
        parser.add_argument('--payload')
        parser.add_argument('--apply', action='store_true')
        args = parser.parse_args()
        if args.command == 'preferences':
            if args.id is not None or args.verb is not None:
                raise ValueError('Preferences do not accept selection arguments.')
            if args.payload is None:
                if args.apply:
                    raise ValueError('Preference writes require a payload.')
                result = {'ok': True, 'message': 'Saved preferences.', **preference_metadata()}
            else:
                if not args.apply:
                    raise ValueError('Explicit --apply is required; nothing changed.')
                patch = payload_object(args.payload)
                if set(patch) == {'reset'} and patch['reset'] is True:
                    prefs = preferences.reset(SLUG, PREFERENCE_SCHEMA)
                else:
                    if isinstance(patch.get('options'), dict) and 'category' in patch['options']:
                        validate_category(patch['options']['category'])
                    prefs = preferences.change(SLUG, PREFERENCE_SCHEMA, patch)
                result = {'ok': True, 'message': 'Preferences saved.', 'preferences': prefs, 'settingsSchema': PREFERENCE_SCHEMA}
        elif args.command == 'catalog':
            if args.id is not None or args.verb is not None or args.apply or args.payload is not None:
                raise ValueError('Catalog does not accept action arguments.')
            result = catalog()
        elif args.verb == 'favorite':
            if not args.apply or args.payload is not None:
                raise ValueError('Favorite requires --apply and no payload.')
            _, known = app_rows(with_previews=False)
            if args.id not in known:
                raise ValueError('Selection is unknown; refresh the catalogue.')
            result = {'ok': True, 'message': 'Favorite updated.', 'preferences': preferences.toggle(SLUG, PREFERENCE_SCHEMA, args.id)}
        else:
            if args.payload is not None:
                raise ValueError('This action does not accept a payload.')
            if not args.apply:
                raise ValueError('Explicit --apply is required; nothing changed.')
            if args.verb != 'launch':
                raise ValueError('Unsupported action; only launch is available.')
            _, paths = app_rows(with_previews=False)
            path = paths.get(args.id)
            if path is None:
                raise ValueError('Selection is unknown or no longer available; refresh the catalogue.')
            if isinstance(path, configparser.ConfigParser):
                with tempfile.TemporaryDirectory(prefix='app-deck-action-') as temporary:
                    selected = Path(temporary) / 'selected.desktop'
                    with selected.open('w', encoding='utf-8') as stream:
                        path.write(stream)
                    selected.chmod(0o600)
                    run_helper(['gio', 'launch', str(selected)])
            else:
                run_helper(['gio', 'launch', str(path)])
            result = {'ok': True, 'message': 'GIO accepted the selected desktop entry; application startup is not confirmed.'}
        if args.command == 'action' and args.verb == 'launch':
            try:
                result['preferences'] = preferences.remember(SLUG, PREFERENCE_SCHEMA, args.id)
            except (ValueError, OSError, UnicodeError):
                result['message'] += ' Warning: recent selection could not be saved.'
        code = 0
    except (ValueError, OSError, UnicodeError) as error:
        result = {'ok': False, 'message': str(error)[:240] if isinstance(error, ValueError) else 'Local data is unavailable; check its permissions and refresh.'}
        code = 1
    try:
        text = preferences.encode_reply(result)
    except ValueError:
        text = preferences.encode_reply({'ok': False, 'message': 'Catalogue exceeds the 1 MiB output limit.'})
        code = 1
    print(text)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
