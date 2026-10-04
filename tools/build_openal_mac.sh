#!/bin/bash
# Build OpenAL Soft for macOS: `vendor/openal-mac/libopenal.dylib`.
#
# The Windows build carries `vendor/openal/soft_oal.dll`; the Mac needs the same
# OpenAL Soft, built for macOS, at `vendor/openal-mac/libopenal.dylib`, which is
# what `spatial_audio.py` loads through `host.openal_library`.  Both platforms
# get the same library, the same HRTF request, and the same mix.
#
# A dylib for this machine is committed, so this is only needed when building a
# fresh checkout on a different architecture or a different openal-soft release.
# Run it once, commit the result.
#
# Usage:
#     tools/build_openal_mac.sh [--source DIR] [--arch arm64|x86_64]
#
#   --source DIR   an openal-soft source tree to build.  Default: fetch the
#                  pinned 1.25.2 release into vendor/build-openal/.
#   --arch ARCH    the architecture to build for.  Default: this machine's.
#
# Requires cmake and a C++ toolchain (Xcode command line tools).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=1.25.2
SOURCE="${ROOT}/vendor/build-openal/openal-soft"
BUILD="${ROOT}/vendor/build-openal/build-mac"
ARCH="$(uname -m)"
if [[ "${1:-}" == "--source" ]]; then SOURCE="$2"; shift 2; fi
if [[ "${1:-}" == "--arch" ]]; then ARCH="$2"; shift 2; fi

if [[ ! -f "${SOURCE}/CMakeLists.txt" ]]; then
  echo "Fetching OpenAL Soft ${VERSION} into vendor/build-openal/..."
  mkdir -p "$(dirname "${SOURCE}")"
  curl -fL "https://github.com/kcat/openal-soft/archive/refs/tags/${VERSION}.tar.gz" \
    | tar xz -C "$(dirname "${SOURCE}")"
  mv "$(dirname "${SOURCE}")/openal-soft-${VERSION}" "${SOURCE}"
fi

echo "Configuring ($(uname -m)) ..."
cmake -S "${SOURCE}" -B "${BUILD}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=ON \
  -DALSOFT_UTILS=OFF \
  -DALSOFT_EXAMPLES=OFF \
  -DALSOFT_TESTS=OFF \
  -DALSOFT_DLOPEN=ON \
  -DCMAKE_OSX_ARCHITECTURES="${ARCH}"

cmake --build "${BUILD}" --config Release

mkdir -p "${ROOT}/vendor/openal-mac"
install -m 755 "${BUILD}/libopenal.dylib" "${ROOT}/vendor/openal-mac/libopenal.dylib"

echo
echo "Built for ${ARCH}:"
ls -l "${ROOT}/vendor/openal-mac/libopenal.dylib"
echo "Commit it so a fresh clone runs without a toolchain."