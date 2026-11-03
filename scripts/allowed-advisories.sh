#!/usr/bin/env bash
# Print the advisory IDs accepted in security/allowlist.txt, one per line.
# Exit with status 1 when an entry is past its review date, so accepted findings are looked at again.
set -euo pipefail

allowlist="$(dirname "$0")/../security/allowlist.txt"
today="$(date -u +%F)"

awk -v today="$today" '
  /^[[:space:]]*(#|$)/ { next }
  $2 < today { printf "expired advisory allowance: %s (review by %s)\n", $1, $2 > "/dev/stderr"; expired = 1; next }
  { print $1 }
  END { exit expired }
' "$allowlist"
