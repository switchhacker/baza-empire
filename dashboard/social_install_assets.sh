#!/usr/bin/env bash
# Social Studio v2 asset installer. Idempotent: safe to re-run.
set -euo pipefail

cd "$(dirname "$0")"
FONTS_DIR="$(pwd)/static/fonts"
mkdir -p "$FONTS_DIR"

INTER_BOLD="$FONTS_DIR/Inter-Bold.ttf"
INTER_REG="$FONTS_DIR/Inter-Regular.ttf"

fetch_inter_fonts() {
    # Already have real fonts?
    if [[ -f "$INTER_BOLD" && $(stat -c%s "$INTER_BOLD") -gt 50000 ]] && \
       [[ -f "$INTER_REG" && $(stat -c%s "$INTER_REG") -gt 50000 ]]; then
        echo "ok: Inter fonts already installed ($(stat -c%s "$INTER_BOLD") + $(stat -c%s "$INTER_REG") bytes)"
        return
    fi

    echo "fetch: Inter fonts from github.com/rsms/inter release"
    local tmpdir=$(mktemp -d)
    trap "rm -rf $tmpdir" EXIT

    local zip="$tmpdir/inter.zip"
    curl -fsSL --retry 3 \
        "https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip" \
        -o "$zip"

    cd "$tmpdir"
    unzip -q "$zip" "extras/ttf/Inter-Bold.ttf" "extras/ttf/Inter-Regular.ttf"

    cp "extras/ttf/Inter-Bold.ttf" "$INTER_BOLD"
    cp "extras/ttf/Inter-Regular.ttf" "$INTER_REG"

    cd - > /dev/null
    echo "  installed Inter-Bold.ttf ($(stat -c%s "$INTER_BOLD") bytes)"
    echo "  installed Inter-Regular.ttf ($(stat -c%s "$INTER_REG") bytes)"
}

fetch_inter_fonts

echo "Social Studio asset install complete."
