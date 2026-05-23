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


# Piper TTS voices
VOICES_DIR="$(pwd)/static/social/piper-voices"
mkdir -p "$VOICES_DIR"

PIPER_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main"
download_piper_voice() {
    local voice="$1"
    local path="$VOICES_DIR/$voice.onnx" cfg="$VOICES_DIR/$voice.onnx.json"
    if [[ -f "$path" && -f "$cfg" ]]; then
        echo "ok: piper voice $voice"; return
    fi
    case "$voice" in
        en_US-amy-medium)   url="$PIPER_BASE/en/en_US/amy/medium/en_US-amy-medium.onnx" ;;
        en_US-ryan-high)    url="$PIPER_BASE/en/en_US/ryan/high/en_US-ryan-high.onnx" ;;
        en_GB-jenny_dioco-medium) url="$PIPER_BASE/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx" ;;
        en_US-lessac-medium)url="$PIPER_BASE/en/en_US/lessac/medium/en_US-lessac-medium.onnx" ;;
        *) echo "unknown voice $voice"; return 1 ;;
    esac
    echo "fetch: $url"
    curl -fsSL --retry 3 "$url" -o "$path.tmp" && mv "$path.tmp" "$path" || { echo "warn: $voice download failed"; return 1; }
    curl -fsSL --retry 3 "$url.json" -o "$cfg.tmp" && mv "$cfg.tmp" "$cfg" || true
    echo "  installed $(stat -c%s "$path") bytes"
}
download_piper_voice en_US-amy-medium || echo "warn: amy voice failed"
download_piper_voice en_US-ryan-high || echo "warn: ryan voice failed"
download_piper_voice en_GB-jenny_dioco-medium || echo "warn: jenny voice failed"
download_piper_voice en_US-lessac-medium || echo "warn: lessac voice failed"

# LUTs (.cube files) — generate programmatically
LUTS_DIR="$(pwd)/static/social/luts"
mkdir -p "$LUTS_DIR"

write_lut() {
    local name="$1" python_expr="$2"
    local out="$LUTS_DIR/$name.cube"
    [[ -f "$out" ]] && { echo "ok: lut $name"; return; }
    python3 -c "
N = 33
print('TITLE \"$name\"')
print('LUT_3D_SIZE', N)
for b in range(N):
    for g in range(N):
        for r in range(N):
            R, G, B = r/(N-1), g/(N-1), b/(N-1)
            $python_expr
            R, G, B = max(0,min(1,R)), max(0,min(1,G)), max(0,min(1,B))
            print(f'{R:.6f} {G:.6f} {B:.6f}')
" > "$out"
    echo "  generated $name.cube ($(wc -l < "$out") lines)"
}

write_lut "cinematic" "R = R * 0.95 + 0.05 * 0.4; G = G * 0.98; B = B * 1.05"
write_lut "vibrant"   "L = 0.3*R + 0.59*G + 0.11*B; R = L + (R - L) * 1.3; G = L + (G - L) * 1.3; B = L + (B - L) * 1.3"
write_lut "moody"     "R = R * 0.8; G = G * 0.85; B = B * 1.1"
write_lut "bw"        "L = 0.3*R + 0.59*G + 0.11*B; R, G, B = L, L, L"
write_lut "warm"      "R = R * 1.1; G = G * 1.02; B = B * 0.88"

# SFX library dir
SFX_DIR="$(pwd)/static/social/sfx"
mkdir -p "$SFX_DIR"
[[ -f "$SFX_DIR/.gitkeep" ]] || touch "$SFX_DIR/.gitkeep"

# Music free dir
MUSIC_DIR="$(pwd)/static/social/music/free"
mkdir -p "$MUSIC_DIR"
[[ -f "$MUSIC_DIR/.gitkeep" ]] || touch "$MUSIC_DIR/.gitkeep"

echo "Social Studio v2.1 asset install complete."
