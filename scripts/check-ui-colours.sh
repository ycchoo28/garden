#!/usr/bin/env bash
# Fail when a frontend file sets a colour directly instead of through the theme tokens (docs/ui.md, "Colour").
# Tokens live in frontend/src/styles.css and frontend/src/shared/ui/theme.ts, so only those two may name colours.
set -euo pipefail

cd "$(dirname "$0")/../frontend"

palette='slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|white|black'
raw="(^|[^a-z0-9_-])[a-z:-]*(bg|text|border|ring|outline|divide|from|via|to|fill|stroke|decoration|accent|caret|placeholder|shadow)(-[a-z]+)?-(${palette})(-[0-9]{2,3})?\b"
literal='#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b|\b(rgba?|hsla?|oklch|oklab)\('

status=0
while IFS= read -r file; do
  hits="$(grep -n -E -e "$raw" -e "$literal" "$file" || true)"
  if [[ -n "$hits" ]]; then
    echo "$file uses a raw colour; use a token class such as text-muted or bg-warn-soft (docs/ui.md):" >&2
    echo "$hits" | sed 's/^/  /' >&2
    status=1
  fi
done < <(find src -type f \( -name '*.vue' -o -name '*.ts' -o -name '*.css' \) \
  -not -path 'src/api/generated/*' -not -path 'src/styles.css' -not -path 'src/shared/ui/theme.ts' | sort)

exit "$status"
