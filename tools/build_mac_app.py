"""Assemble the double-clickable Mac game: ``Play Soul Trapper.app``.

The Windows build ships as a ZIP - ``Soul Trapper.exe`` plus ``_internal/`` plus
``runtime/``, with ``Launcher.cs`` starting the bundled interpreter.  The Mac
needs no different game, only a different wrapper: the same tree, carried
inside a ``.app`` bundle so Finder will start it, with the Mac counterpart of
``Launcher.cs`` in ``Contents/MacOS``.

The layout inside is deliberately identical to the Windows one, because every
path in the game is relative to ``game.py``:

    Play Soul Trapper.app/Contents/
        MacOS/Play Soul Trapper              the launcher (a bash script)
        Info.plist                          bundle identity and version
        PkgInfo                             what Finder reads first
        Resources/Soul Trapper/
            _internal/game.py                and every other module, plus
                                            assets/, data/, vendor/ - the game
                                            resolves those against its own
                                            folder, so they live inside it
            runtime/bin/python3             the bundled Python
            portable.txt                     only with --portable

What is *not* mirrored is where saves go.  A ``.app`` on the Mac is routinely
somewhere nothing inside it can be written to (``/Applications``, or the
read-only mount App Translocation puts a quarantined download on), so
``saves.py`` uses ``~/Library/Application Support/Soul Trapper`` unless
``portable.txt`` says otherwise.  ``--portable`` writes that marker, for a copy
that is deliberately kept somewhere writable.

The usual case is one flag, and it needs nothing prepared:

    python tools/build_mac_app.py --source ~/src/soul-trapper-game

``--source`` is the prepared game folder - the same one that becomes the
Windows ZIP, with ``_internal/`` in it.  Everything else is arranged: the
bundled Python is built by ``tools/build_mac_runtime.sh``, which gets a
standalone interpreter from uv and merges in the dependencies from ``uv.lock``,
because a virtualenv cannot be carried anywhere (see that script for why).
``--runtime DIR`` reuses a runtime instead of building one, and ``--runtime sys``
carries none at all, which makes a bundle that only runs on this machine.

Gatekeeper: an unsigned bundle built here will be refused on first open on any
Mac with a recent macOS.  The player either right-clicks it and chooses Open,
or it is signed and notarised - both are outside what this script can decide.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import host                                              # noqa: E402

APP_NAME = 'Play Soul Trapper'
BUNDLE_ID = 'com.soultrapper.port'
GAME_FOLDER = 'Soul Trapper'

#: Copied out of ``--source``.
#:
#: Only ``_internal/``, because that is the whole of the game's own tree: the
#: game resolves every path against ``game.py``'s own folder, so ``assets/``,
#: ``data/`` and ``vendor/`` live *inside* ``_internal/`` in the Windows ZIP and
#: must be copied with it.  Carrying them as siblings too would put a second,
#: unused copy of the game's audio in the bundle.
#:
#: ``runtime/`` is the one thing that is a sibling of ``_internal/``, and it is
#: staged separately because ``--runtime`` may point somewhere else.
COPIED = ('_internal',)

#: Must be in ``--source/_internal/``, or the game cannot start.  ``game.py`` is
#: what the launcher runs; ``host.py`` is where every platform decision now
#: lives, and a prepared folder made before that existed would fail at the first
#: import rather than here.
REQUIRED = ('game.py', 'host.py')

#: Everything the game reads at run time, all of it inside ``_internal/``.  These
#: are checked because their absence is not a build failure, it is a game that
#: starts and then cannot find its own audio.
EXPECTED = ('assets', 'data', 'vendor')

LAUNCHER = '''#!/bin/bash
# The Mac counterpart of Launcher.cs: start the bundled interpreter, and if the
# game dies where we can see it, say so and leave a log where the player can
# find it.  The game writes its own error.log for failures it survives.
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../Resources/{game}" && pwd)"
cd "$ROOT" || exit 1
PY="$ROOT/runtime/bin/python3"
[ -x "$PY" ] || PY="{python}"
LOGDIR="$HOME/Library/Application Support/{game}"
if ! mkdir -p "$LOGDIR" 2>/dev/null || [ ! -w "$LOGDIR" ]; then LOGDIR="$ROOT"; fi
OUTPUT="$("$PY" -E -s -X faulthandler -u "$ROOT/_internal/game.py" --launcher "$@" 2>&1)"
STATUS=$?
# Finder gives us no console to print to, so the output is kept for the log.
# Run from a terminal - while building - and it is passed through instead.
[ -t 1 ] && printf '%s\\n' "$OUTPUT"
if [ $STATUS -ne 0 ]; then
  {{
    printf '\\nSoul Trapper {version} launcher\\n'
    date -u '+%Y-%m-%dT%H:%M:%SZ'
    sw_vers 2>/dev/null
    printf 'Process exit code: %d (0x%08X)\\n' $STATUS $STATUS
    printf '%s\\n' "$OUTPUT"
  }} >> "$LOGDIR/error.log" 2>/dev/null
  /usr/bin/osascript -e "display alert \\"Soul Trapper closed unexpectedly. Please send error.log to the developer.\\" message \\"Error log: $LOGDIR/error.log\\"" >/dev/null 2>&1
fi
exit $STATUS
'''


def resolve_python(runtime: str, resources: str, python_version: str = '') -> str:
    """The interpreter the launcher runs, staging ``runtime/`` if we have one.

    A real ``--runtime`` folder is copied into the bundle and becomes
    ``runtime/bin/python3``, exactly the shape the Windows build's
    ``runtime/python.exe`` has, so the game itself cannot tell them apart.

    Three ways to get one, in increasing order of what you have to think about:

    ``uv``       build one with ``tools/build_mac_runtime.sh`` (the default).
    a folder     used as it is - a runtime you built earlier, or your own.
    ``sys``      carry nothing, and run the interpreter running this script:
                 a development build that only works on this machine.
    """
    if runtime == 'sys':
        print(f'    no runtime carried: the launcher runs {sys.executable}')
        return sys.executable
    if runtime == 'uv':
        runtime = build_runtime(resources, python_version)
    elif not os.path.isdir(runtime):
        raise SystemExit(f'no such runtime folder: {runtime}\n'
                         'Pass --runtime uv to build one, --runtime DIR to use '
                         'one you already have, or --runtime sys for a '
                         'development build.')
    destination = os.path.abspath(os.path.join(resources, 'runtime'))
    if os.path.isdir(destination):
        shutil.rmtree(destination)
    print(f'    staging runtime: {runtime}')
    shutil.copytree(runtime, destination, symlinks=True)
    if not os.path.isfile(os.path.join(destination, 'bin', 'python3')):
        raise SystemExit(f'that runtime has no bin/python3: {destination}\n'
                         'A virtualenv cannot be used here - its pyvenv.cfg '
                         'names an absolute interpreter, so it stops working '
                         'when it is copied. Build one with:\n'
                         '    tools/build_mac_runtime.sh DIR')
    return destination


def build_runtime(resources: str, python_version: str) -> str:
    """Ask ``build_mac_runtime.sh`` for a runtime, in a scratch folder."""
    script = os.path.join(ROOT, 'tools', 'build_mac_runtime.sh')
    if not os.path.isfile(script):
        raise SystemExit(f'missing {script}')
    # Built outside the bundle and copied in, so a failed build leaves no
    # half-populated runtime inside it.
    scratch = tempfile.mkdtemp(prefix='soultrapper-runtime-')
    try:
        command = ['bash', script, scratch]
        if python_version:
            command.append(python_version)
        print('    ', ' '.join(command))
        # Flushed first: the child writes straight to the terminal, and without
        # this its output arrives before the line announcing it.
        sys.stdout.flush()
        subprocess.run(command, check=True,
                       env=dict(os.environ, SOULTRAPPER_NESTED='1'))
    except subprocess.CalledProcessError as e:
        raise SystemExit(f'building the runtime failed ({e.returncode})')
    except FileNotFoundError:
        raise SystemExit('bash is not available')
    return scratch


def check_bundle_imports(resources: str, runtime: str) -> None:
    """The assembled bundle must import the game with the runtime it carries.

    This is the check that earns its keep.  Everything else about a bundle looks
    right whether or not it can start: the layout is correct, the plist is
    stamped, the interpreter is there.  A dependency missing from the runtime -
    unicorn, say, which engine.py needs to emulate data/SoulTrapper.arm - is
    invisible until the player opens it, and then it is a ModuleNotFoundError on
    the first frame with nothing in the build to explain it.

    Cheap, because the runtime is already built by this point: this is one
    interpreter run, not another build.
    """
    modules = sorted(p.stem for p in Path(resources, '_internal').glob('*.py'))
    python = os.path.join(runtime, 'bin', 'python3')
    if not os.path.isfile(python):
        return                          # --runtime sys: nothing was carried
    print(f'    checking the bundle can import {len(modules)} modules ...')
    done = subprocess.run(
        [python, '-W', 'ignore::DeprecationWarning', '-c',
         'import ' + ', '.join(modules)],
        cwd=os.path.join(resources, '_internal'), capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(
            'the bundle cannot import its own game with the runtime it carries:\n'
            + (done.stderr.strip() or done.stdout.strip()) +
            '\nthe layout is fine; a dependency is missing from the runtime. '
            'Is it in pyproject.toml?')


def build(source: str, runtime: str, out: str, portable: bool,
          python_version: str = '') -> str:
    if not os.path.isdir(source):
        raise SystemExit(f'no such source folder: {source}\n'
                         'it is the prepared game folder, with _internal/ in it')
    internal = os.path.join(source, '_internal')
    if not os.path.isdir(internal):
        raise SystemExit(f'{source} has no _internal/\n'
                         'it does not look like a prepared game folder')
    absent = [name for name in REQUIRED
              if not os.path.isfile(os.path.join(internal, name))]
    if absent:
        raise SystemExit(f'{source}/_internal/ is missing ' + ', '.join(absent) +
                         '\nthe game needs these; it looks like an older game '
                         'folder, or the wrong one.')
    missing = [name for name in EXPECTED
               if not os.path.isdir(os.path.join(internal, name))]
    if missing:
        print('    WARNING: no ' + ', '.join(missing) + ' in _internal/; the '
              'bundle will build, but the game will not find its own '
              + ('audio' if missing == ['assets'] else 'data'))

    bundle = os.path.join(out, APP_NAME + '.app')
    if os.path.exists(bundle):
        shutil.rmtree(bundle)
    contents = os.path.join(bundle, 'Contents')
    resources = os.path.join(contents, 'Resources', GAME_FOLDER)
    macos = os.path.join(contents, 'MacOS')
    for folder in (macos, resources):
        os.makedirs(folder, exist_ok=True)

    print(f'    copying the game into Contents/Resources/{GAME_FOLDER}/ ...')
    for name in COPIED:
        shutil.copytree(os.path.join(source, name),
                        os.path.join(resources, name), symlinks=True)
    if portable:
        # An explicit request to keep saves beside the game, which only works if
        # the .app itself is somewhere writable. See saves.py.
        open(os.path.join(resources, 'portable.txt'), 'w', encoding='utf8').close()

    executable = resolve_python(runtime, resources, python_version)
    check_bundle_imports(resources, executable)

    launcher = os.path.join(macos, APP_NAME)
    with open(launcher, 'w', encoding='utf8') as fh:
        fh.write(LAUNCHER.format(game=GAME_FOLDER, python=executable,
                                 version=host.VERSION))
    # PyInstaller's windowed Mac builds leave the inner binary non-executable,
    # and a bundle whose CFBundleExecutable cannot be exec'd does not open.
    os.chmod(launcher, os.stat(launcher).st_mode | stat.S_IXUSR | stat.S_IXGRP)

    # Finder reads PkgInfo first; not every builder writes it.
    with open(os.path.join(contents, 'PkgInfo'), 'w', encoding='ascii') as fh:
        fh.write('APPL????')
    with open(os.path.join(contents, 'Info.plist'), 'wb') as fh:
        plistlib.dump({
            'CFBundleName': APP_NAME,
            'CFBundleDisplayName': APP_NAME,
            # PyInstaller's default would be the bare app name, which is not a
            # sane identifier - and has a space in it, at that. This is what
            # LaunchServices, Spotlight and uninstall tooling key on.
            'CFBundleIdentifier': BUNDLE_ID,
            'CFBundleExecutable': APP_NAME,
            'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': host.VERSION,
            'CFBundleVersion': host.VERSION,
            'LSMinimumSystemVersion': '11.0',
            'NSHighResolutionCapable': True,
        }, fh)

    size = sum(os.path.getsize(os.path.join(base, name))
               for base, _dirs, names in os.walk(bundle) for name in names)
    print(f'    {bundle}  ({size / 1e6:.0f} MB)')
    return bundle


def parser() -> argparse.ArgumentParser:
    """The command line, on its own, so the defaults can be tested."""
    p = argparse.ArgumentParser(
        prog='build_mac_app.py',
        description=__doc__.splitlines()[0],
        epilog='the usual case needs one flag:\n'
               '    python tools/build_mac_app.py --source DIR\n'
               'which builds the bundle *and* a runtime for it.')
    p.add_argument('--source', required=True, metavar='DIR',
                   help='the prepared game folder, the same one that '
                        'becomes the Windows ZIP')
    p.add_argument('--runtime', default='uv', metavar='uv|DIR|sys',
                   help='where the bundled Python comes from: '
                        '"uv" builds one with tools/build_mac_runtime.sh '
                        '(default), DIR uses one you already have, "sys" '
                        'carries nothing and runs this interpreter, which '
                        'makes a build that only works on this machine')
    p.add_argument('--python', default='', metavar='VERSION',
                   help='the Python version for a runtime uv builds '
                        '(default: 3.12, the last with audioop)')
    p.add_argument('--out', default=os.path.join(ROOT, 'dist'), metavar='DIR',
                   help='where to write the bundle (default: dist/)')
    p.add_argument('--portable', action='store_true',
                   help='keep saves beside the game instead of in '
                        'Application Support, by writing portable.txt. '
                        'Only if the .app itself is somewhere writable')
    return p


def main() -> int:
    args = parser().parse_args()
    if not host.MAC:                                # pragma: no cover
        raise SystemExit('this builds a macOS bundle; run it on a Mac')
    if args.python and args.runtime != 'uv':
        raise SystemExit('--python only applies to --runtime uv')
    os.makedirs(args.out, exist_ok=True)
    print(f'\n=== building {APP_NAME}.app ===', flush=True)
    bundle = build(args.source, args.runtime, args.out, args.portable,
                   args.python)
    print(f'\nDone:  {bundle}')
    print('Double-click it in Finder. Gatekeeper will refuse it the first time: '
          'right-click\nthe bundle and choose Open. Signing and notarising it '
          'is a separate job.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())