#!/usr/bin/env bash
# Build one policy version into a binary: build.sh versions/v001.lg  -> prints the binary path.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
src="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
proj="$here/policybuild"
# Uses `largo` (LOGOS 0.11) from PATH. Set LOGOS_WORKSPACE if your largo build needs a LOGOS source checkout.
if [ ! -f "$proj/Largo.toml" ]; then
  (cd "$here" && largo new policybuild >/dev/null)
fi
cat "$here/harness_head.lg" "$src" "$here/harness_main.lg" > "$proj/src/main.lg"
(cd "$proj" && largo build >"$proj/build.log" 2>&1) || { tail -30 "$proj/build.log" >&2; exit 1; }
name="$(basename "$src" .lg)"
mkdir -p "$here/bin"
cp "$proj/target/debug/policybuild" "$here/bin/$name"
echo "$here/bin/$name"
