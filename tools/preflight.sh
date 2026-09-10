#!/usr/bin/env bash
# Run every harness for one mod before it is installed or published.
#
#   preflight.sh <repo dir> [installed folder]
#
# Exits non-zero if any harness fails. A clean run is not proof: each harness is
# validated against a known-broken input, and that validation lives in the repo
# history, not in the exit code.
set -uo pipefail

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${1:?usage: preflight.sh <repo dir> [installed folder]}"
INSTALL="${2:-}"
NAME="$(basename "$REPO")"

ZIP="$(ls -1 "$REPO"/Thunderstore/potto007-*.zip 2>/dev/null | sort -V | tail -1)"
STATUS=0

run() {
  local label="$1"; shift
  echo "=============== $label"
  "$@" || STATUS=1
  echo
}

echo "preflight: $NAME"
echo

if [ -n "$ZIP" ]; then
  run "package"    python3 "$TOOLS/verify_package.py" "$ZIP"
else
  echo "=============== package"; echo "SKIP  no built package in $REPO/Thunderstore"; echo
fi

run "enum constants" python3 "$TOOLS/check_enums.py" "$REPO"
# The PascalCase naming rule belongs to mods that opted into it, so the check is off
# unless asked for. A mod that keeps its upstream setting names must not fail here.
if [ "${CHECK_CONFIG_NAMES:-0}" = "1" ]; then
  run "config names" python3 "$TOOLS/check_config_names.py" "$REPO"
else
  echo "=============== config names"; echo "SKIP  set CHECK_CONFIG_NAMES=1 to run it"; echo
fi

if [ -n "$INSTALL" ] && [ -n "$ZIP" ]; then
  run "install" python3 "$TOOLS/verify_install.py" "$ZIP" "$INSTALL"
  if [ "$STATUS" -ne 0 ]; then
    echo "To make the folder match the package (layout only, records untouched):"
    echo "  python3 \"$TOOLS/repair_install.py\" \"$ZIP\" \"$INSTALL\" --apply"
    echo
  fi
else
  echo "=============== install"; echo "SKIP  no installed folder given"; echo
fi

if [ "$STATUS" -eq 0 ]; then
  echo "PREFLIGHT PASSED: $NAME"
else
  echo "PREFLIGHT FAILED: $NAME"
fi
exit "$STATUS"
