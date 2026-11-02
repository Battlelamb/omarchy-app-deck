#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Read known XDG desktop entries; delegate execution to the installed GIO."""
from itertools import islice
from pathlib import Path
import argparse
import configparser
import json
import os
import re
import shutil
import signal
import stat
import subprocess


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


def app_rows(with_previews=True):
    rows, paths, seen = [], {}, set()
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
            image = app_icon(entry.get('Icon', '')) if with_previews else None
            if image:
                row['preview'] = image
            rows.append(row)
            paths[ident] = path
            if len(rows) >= 4096:
                return rows, paths
    return rows, paths


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
    rows, _ = app_rows()
    shown = displayed_rows(rows)
    limited = ' Display limited; full scanned membership remains validated for actions.' if len(shown) < len(rows) else ''
    return {'rows': shown, 'message': f'{len(rows)} installed desktop applications (bounded XDG scan).'+limited, 'actions':['launch'], 'actionLabel':'Launch'}


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


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('Invalid arguments; use catalog or action --id ID --verb launch --apply.')


def main():
    try:
        parser = Parser(allow_abbrev=False, add_help=False)
        parser.add_argument('command', choices=['catalog', 'action'])
        parser.add_argument('--id', default='')
        parser.add_argument('--verb', default='')
        parser.add_argument('--apply', action='store_true')
        args = parser.parse_args()
        if args.command == 'catalog':
            if args.id or args.verb or args.apply:
                raise ValueError('Catalog does not accept action arguments.')
            result = catalog()
        else:
            if not args.apply:
                raise ValueError('Explicit --apply is required; nothing changed.')
            if args.verb != 'launch':
                raise ValueError('Unsupported action; only launch is available.')
            _, paths = app_rows(with_previews=False)
            path = paths.get(args.id)
            if path is None:
                raise ValueError('Selection is unknown or no longer available; refresh the catalogue.')
            run_helper(['gio', 'launch', str(path)])
            result = {'ok': True, 'message': 'GIO accepted the selected desktop entry; application startup is not confirmed.'}
        code = 0
    except (ValueError, OSError, UnicodeError) as error:
        result = {'ok': False, 'message': str(error)[:240] if isinstance(error, ValueError) else 'Local data is unavailable; check its permissions and refresh.'}
        code = 1
    text = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
    if len(text.encode('utf-8')) > 1048575:
        text = json.dumps({'ok': False, 'message': 'Catalogue exceeds the 1 MiB output limit.'})
        code = 1
    print(text)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
