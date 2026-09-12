#!/usr/bin/env bash
# Build one mod, and fail on what a piped or filtered build hides.
#
#   build.sh <repo dir> [compile|debug|release] [extra dotnet build args]
#
#   compile  Debug build that copies nothing into a profile (-p:CopyOutputDLLPath=)
#   debug    Debug build copied into the profile the mod's props name, then
#            check_deploy.py to show whether the game loads from that folder
#   release  Release build and package, then verify_package.py on the zip. It runs
#            from here because the template's own call is skipped in a worktree
#
# The full log is kept at <repo>/obj/build-<mode>.log. The last line is BUILD PASSED
# or BUILD FAILED, and the exit code matches. Gate on the exit code, never on a grep
# of the output: `dotnet build | grep -E "error|Build succeeded" && git commit`
# once committed a failed build, because grep exits 0 when it matches error lines.
set -uo pipefail

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${1:?usage: build.sh <repo dir> [compile|debug|release] [dotnet build args]}"
MODE="${2:-debug}"
shift $(( $# >= 2 ? 2 : 1 ))

PROJECT="$REPO/$(basename "$(cd "$REPO" && pwd)").csproj"
[ -f "$PROJECT" ] || PROJECT="$(ls -1 "$REPO"/*.csproj 2>/dev/null | head -1)"
if [ -z "$PROJECT" ]; then
  echo "no .csproj in $REPO"; echo "BUILD FAILED"; exit 1
fi

case "$MODE" in
  compile) CONFIG=Debug;   EXTRA=(-p:CopyOutputDLLPath=) ;;
  debug)   CONFIG=Debug;   EXTRA=() ;;
  release) CONFIG=Release; EXTRA=() ;;
  *) echo "unknown mode: $MODE (compile, debug or release)"; exit 2 ;;
esac

# Valheim holds the profile DLL open, so a Debug copy would fail on a locked file.
if [ "$MODE" = debug ] && tasklist.exe /FI "IMAGENAME eq valheim.exe" /NH 2>/dev/null \
    | grep -qi valheim.exe; then
  echo "Valheim is running and holds the profile DLL open. Quit it, or use compile."
  echo "BUILD FAILED"; exit 1
fi

mkdir -p "$REPO/obj"
LOG="$REPO/obj/build-$MODE.log"
STAMP="$REPO/obj/build-$MODE.stamp"
touch "$STAMP"

dotnet build "$PROJECT" -c "$CONFIG" "${EXTRA[@]}" "$@" 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}
STATUS=0
echo
if [ "$RC" -ne 0 ]; then
  echo "dotnet build exited $RC"; STATUS=1
fi
if grep -q ': error ' "$LOG"; then
  echo "errors in the build log:"
  grep ': error ' "$LOG" | sort -u | head -20
  STATUS=1
fi

if [ "$STATUS" -eq 0 ] && [ "$MODE" = release ]; then
  VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8-sig"))["version_number"])' "$REPO/Thunderstore/manifest.json")"
  ZIP="$(ls -1 "$REPO"/Thunderstore/*-"$VERSION".zip 2>/dev/null | head -1)"
  if [ -z "$ZIP" ] || [ ! "$ZIP" -nt "$STAMP" ]; then
    echo "no package for $VERSION was written by this build"; STATUS=1
  else
    echo "=============== package"
    python3 "$TOOLS/verify_package.py" "$ZIP" || STATUS=1
  fi
fi

if [ "$STATUS" -eq 0 ] && [ "$MODE" = debug ]; then
  echo "=============== deploy target"
  python3 "$TOOLS/check_deploy.py" "$REPO" || STATUS=1
fi

echo "log: $LOG"
if [ "$STATUS" -eq 0 ]; then echo "BUILD PASSED"; else echo "BUILD FAILED"; fi
exit "$STATUS"
