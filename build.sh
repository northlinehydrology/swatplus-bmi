#!/usr/bin/env bash
# Build SWAT+ with the exchange mode for swatplus-bmi.
#
# Fetches the tagged SWAT+ release, copies in the exchange module, applies
# the patch for that release and builds with CMake. With --stock it also
# builds the unpatched release from the same sources, for comparison tests.
#
#   ./build.sh                 # bin/swatplus-62.0.1-exchange
#   ./build.sh --stock         # also bin/swatplus-62.0.1-stock
#   ./build.sh --nosave        # also bin/swatplus-62.0.1-nosave: stock with every saved
#                              # local reset on each call except those in carried-locals.toml
#   ./build.sh --jobs 2        # parallel compile jobs (default 2)
#   SWATPLUS_SRC=/path ./build.sh   # use an existing checkout of the tag
#
# Supported SWAT+ versions are the ones with a folder in patches/ (see
# COMPATIBILITY.md). To try another version or branch with a supported
# version's patch, for example SWAT+ main in the nightly build:
#
#   ./build.sh --tag main --patch 62.0.1 --stock
#
# Needs git, cmake (3.22 or later), make, gfortran (or ifx) and Python 3.11 or
# later. With uv, `uv sync` then `uv run ./build.sh` provides cmake and Python.

set -euo pipefail

TAG="62.0.1"
JOBS=2
STOCK=0
NOSAVE=0
PATCH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --stock) STOCK=1 ;;
    --nosave) NOSAVE=1 ;;
    --jobs) JOBS="$2"; shift ;;
    --tag) TAG="$2"; shift ;;
    --patch) PATCH="$2"; shift ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

HERE="$(cd "$(dirname "$0")" && pwd)"
PATCHES="$HERE/patches/${PATCH:-$TAG}"
WORK="$HERE/build/$TAG"
BIN="$HERE/bin"
CMAKE="${CMAKE:-cmake}"

[ -d "$PATCHES" ] || { echo "SWAT+ ${PATCH:-$TAG} is not supported: no patch in patches/ (see COMPATIBILITY.md)" >&2; exit 1; }
[ -z "$PATCH" ] || echo "building SWAT+ $TAG with the patch for $PATCH: not a supported combination"
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

# 2b. the unpatched release with no saved locals, to test whether any carry state
if [ "$NOSAVE" = 1 ]; then
  rm -rf "$WORK/nosave-src"
  mkdir -p "$WORK/nosave-src"
  cp -R "$SRC/." "$WORK/nosave-src/"
  "${PYTHON:-python3}" "$HERE/tools/reset_saved_locals.py" "$SRC/src" "$WORK/nosave-src/src" \
    "$PATCHES/carried-locals.toml"
  build "$WORK/nosave-src" "$WORK/nosave-build" "swatplus-$TAG-nosave"
fi

# 3. the release with the exchange mode
rm -rf "$WORK/exchange-src"
mkdir -p "$WORK/exchange-src"
cp -R "$SRC/." "$WORK/exchange-src/"
cp "$HERE"/fortran/*.f90 "$HERE"/fortran/*.c "$WORK/exchange-src/src/"
# the saved locals that carry state, moved into a module, then the state
# serializer, generated from this release's module declarations
"${PYTHON:-python3}" "$HERE/tools/carried_locals.py" "$PATCHES/carried-locals.toml" "$WORK/exchange-src/src"
"${PYTHON:-python3}" "$HERE/tools/gen_serializer.py" "$WORK/exchange-src/src" "$WORK/exchange-src/src/bmi_state_module.f90"
for p in "$PATCHES"/*.patch; do
  patch --quiet -p1 -d "$WORK/exchange-src" < "$p"
done
build "$WORK/exchange-src" "$WORK/exchange-build" "swatplus-$TAG-exchange"
