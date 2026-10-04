#!/bin/bash
# Build a relocatable macOS Python runtime for `Play Soul Trapper.app`.
#
# The bundle needs an interpreter it can carry: one that starts on a machine
# that has never seen this project. That rules out a virtualenv, whose
# `pyvenv.cfg` records an absolute path to the interpreter it was made from,
# so copying it somewhere else gives you a folder that cannot find its own
# standard library.
#
# What works is the standalone interpreter `uv python install` fetches: it
# resolves its standard library relative to its own location, so the folder can
# be copied anywhere - including inside a bundle - and still start.
#
# Dependencies are resolved by uv into a throwaway virtualenv and then merged in,
# because uv refuses to install into its own managed interpreters ("This Python
# installation is managed by uv and should not be modified"). The result is one
# self-contained folder with no reference back to this machine.
#
# Usage:
#     tools/build_mac_runtime.sh OUTDIR [PYTHON_VERSION]
#
#   OUTDIR          where to write the runtime.  The bundle wants it named
#                   `runtime`, holding `bin/python3`.
#   PYTHON_VERSION  default 3.12.
#
# 3.12 is the default and not an accident: `audioop` was removed in 3.13, so
# 3.12 is the last version where the game's own PCM code runs on the standard
# library, which is also what the Windows build does. Pinning both platforms to
# the same version is why `sound_levels.py` has a fallback that is only ever
# reached by a developer on a newer Python.
#
# Requires uv, and nothing else:
#     brew install uv
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:?usage: build_mac_runtime.sh OUTDIR [PYTHON_VERSION]}"
VERSION="${2:-3.12}"
CASE_VERSION="cpython-${VERSION}-macos-$(uname -m)-none"

command -v uv >/dev/null || {
  echo "uv is not installed.  Install it with:  brew install uv" >&2
  exit 1
}

echo "=== building a macOS runtime (Python ${VERSION}, $(uname -m)) ==="

uv python install "$VERSION"
INTERP="$(uv python find "$VERSION")"
SOURCE="$(dirname "$(dirname "$INTERP")")"
[ -d "$SOURCE/lib" ] || { echo "unexpected interpreter layout: $SOURCE" >&2; exit 1; }
echo "    interpreter: $SOURCE"

rm -rf "$OUT"
mkdir -p "$OUT"
# The interpreter, verbatim and relocatable. 65 MB, and nothing in it points
# back here once it has moved.
cp -R "$SOURCE/." "$OUT/"

# Dependencies, resolved by uv. A virtualenv, because that is the only place uv
# will install into - but a throwaway one, whose contents are merged below and
# then discarded. Only site-packages is kept: nothing else in it is wanted, and
# pyvenv.cfg would defeat the whole point by naming an absolute interpreter.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
uv venv --quiet "$WORK/venv" --python "$VERSION"
SITE="$("$WORK/venv/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
echo "    resolving dependencies ..."
if [ -f "$ROOT/uv.lock" ]; then
  # --no-default-groups: the dev group (pyflakes and friends) is for working on
  # the game, not for shipping it. The runtime gets the game's dependencies at
  # the versions uv.lock pins, which is the point of having a lock.
  uv export --project "$ROOT" --frozen --no-emit-project --no-hashes \
      --no-default-groups --format requirements-txt \
      -o "$WORK/requirements.txt" >/dev/null
  uv pip install --python "$WORK/venv/bin/python" -r "$WORK/requirements.txt" --quiet
else
  echo "    WARNING: no uv.lock; versions are not pinned" >&2
  uv pip install --python "$WORK/venv/bin/python" --quiet \
      pygame-ce 'pyobjc-framework-cocoa; sys_platform == "darwin"'
fi

# Where this interpreter looks for site-packages, asked rather than guessed:
# python3.12 has no patch component to strip, so ${VERSION%.*} is "3", not "12".
TAG="$("$OUT/bin/python3" -c 'import sys; print("python%d.%d" % sys.version_info[:2])')"
TARGET="$OUT/lib/$TAG/site-packages"
mkdir -p "$TARGET"
cp -R "$SITE/." "$TARGET/"
echo "    merged $(ls -1 "$SITE" | wc -l | tr -d ' ') packages into lib/$TAG"

[ -x "$OUT/bin/python3" ] || { echo "no bin/python3 in $OUT" >&2; exit 1; }
# The proof that it is relocatable: this must not mention this machine.
# audioop's deprecation warning is expected here - 3.12 is the point.
#
# Only the dependencies can be imported from here: this folder is built on its
# own, before any of the game is copied in. Whether the assembled bundle can
# import the game is build_mac_app.py's check, and it has to be that layer's -
# unicorn is what engine.py needs to emulate data/SoulTrapper.arm, and its
# absence would only ever show up in the bundle, on the player's first frame.
"$OUT/bin/python3" -W ignore::DeprecationWarning -c '
import sys
assert sys.base_prefix == sys.prefix, (sys.base_prefix, sys.prefix)
assert not sys.base_prefix.startswith("'"$HOME"'"), sys.base_prefix
print("    relocatable:", sys.base_prefix)
import pygame, Foundation, AppKit, audioop
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB
print("    pygame-ce", pygame.version.ver,
      "| pyobjc ok | audioop native | unicorn ok")
'

echo
echo "    $OUT  ($(du -sh "$OUT" | cut -f1))"
# Only when run on its own: build_mac_app.py calls this, and there the bundle
# is already being built around it.
[ -z "${SOULTRAPPER_NESTED:-}" ] && cat <<EOF

Now build the bundle around it:
    python tools/build_mac_app.py --source DIR --runtime $OUT
EOF
exit 0