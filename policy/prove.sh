#!/usr/bin/env bash
# Prove the four safety rules for one policy version with the kernel-certified prover, `largo prove`.
# The rules are inequalities over the policy's own functions. That needs a largo build that can state order
# relations in English theorems and apply program functions inside them. Point LARGO_PROVE at that build.
# prove.sh versions/v013.lg  -> writes versions/v013.prove.txt, exits 0 only if all four rules are proven.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
src="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
largo="${LARGO_PROVE:-largo}"
mkdir -p "$here/proofs"
name="$(basename "$src" .lg)"
file="$here/proofs/$name.prove.lg"
cat "$here/harness_head.lg" "$src" "$here/rules.lg" > "$file"
start=$(date +%s)
"$largo" prove "$file" > "${src%.lg}.prove.txt" 2>&1
code=$?
echo "elapsed_seconds $(( $(date +%s) - start ))" >> "${src%.lg}.prove.txt"
exit $code
