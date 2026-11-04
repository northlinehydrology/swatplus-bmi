#!/usr/bin/env bash
# Build SWAT+ with the exchange mode for swatplus-bmi.
#
# Fetches the tagged SWAT+ release, copies in the exchange module, applies
# the patch for that release and builds with CMake. With --stock it also
# builds the unpatched release from the same sources, for comparison tests.
#
#   ./build.sh                 # bin/swatplus-62.0.1-exchange
#   ./build.sh --stock         # also bin/swatplus-62.0.1-stock
#   ./build.sh --jobs 2        # parallel compile jobs (default 2)
#   SWATPLUS_SRC=/path ./build.sh   # use an existing checkout of the tag
#
# Needs git, cmake (3.22 or later), make and gfortran (or ifx).

set -euo pipefail

TAG="62.0.1"
JOBS=2
STOCK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --stock) STOCK=1 ;;
    --jobs) JOBS="$2"; shift ;;
    --tag) TAG="$2"; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

HERE="$(cd "$(dirname "$0")" && pwd)"
PATCHES="$HERE/patches/$TAG"
WORK="$HERE/build/$TAG"
BIN="$HERE/bin"
CMAKE="${CMAKE:-cmake}"

[ -d "$PATCHES" ] || { echo "no patch for SWAT+ $TAG in patches/" >&2; exit 1; }
mkdir -p "$WORK" "$BIN"

# 1. the tagged release, once
SRC="${SWATPLUS_SRC:-$WORK/swatplus}"
if [ ! -d "$SRC/src" ]; then
  git clone --quiet --depth 1 --branch "$TAG" https://github.com/swat-model/swatplus.git "$SRC"
fi

# the build date SWAT+ writes into its output headers: the tag's commit date,
# so every build of a tag writes the same headers
if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
  SOURCE_DATE_EPOCH="$(git -C "$SRC" log -1 --format=%ct 2>/dev/null || date +%s)"
fi
export SOURCE_DATE_EPOCH

build () {   # build <source dir> <build dir> <name in bin/>
  local src="$1" bld="$2" name="$3"
  "$CMAKE" -S "$src" -B "$bld" -DCMAKE_BUILD_TYPE=Release -DTAG="$TAG" > "$bld.configure.log" 2>&1 \
    || { cat "$bld.configure.log" >&2; exit 1; }
  "$CMAKE" --build "$bld" -j "$JOBS" > "$bld.build.log" 2>&1 \
    || { tail -40 "$bld.build.log" >&2; exit 1; }
  local exe
  exe="$(find "$bld" -maxdepth 1 -type f -perm -u+x -name 'swatplus-*' | head -1)"
  cp "$exe" "$BIN/$name"
  echo "built $BIN/$name"
}

# 2. the unpatched release, from a clean copy
if [ "$STOCK" = 1 ]; then
  rm -rf "$WORK/stock-src"
  mkdir -p "$WORK/stock-src"
  cp -R "$SRC/." "$WORK/stock-src/"
  build "$WORK/stock-src" "$WORK/stock-build" "swatplus-$TAG-stock"
fi

# 3. the release with the exchange mode
rm -rf "$WORK/exchange-src"
mkdir -p "$WORK/exchange-src"
cp -R "$SRC/." "$WORK/exchange-src/"
cp "$HERE"/fortran/*.f90 "$WORK/exchange-src/src/"
for p in "$PATCHES"/*.patch; do
  patch --quiet -p1 -d "$WORK/exchange-src" < "$p"
done
build "$WORK/exchange-src" "$WORK/exchange-build" "swatplus-$TAG-exchange"
