#!/usr/bin/env bash
# Only needed when display text in build_assets.py changes. Fonts are OFL.
# Then: pip install fonttools uharfbuzz && python scripts/build_assets.py
set -euo pipefail
dir="$(cd "$(dirname "$0")/.." && pwd)/assets/src/fonts"
mkdir -p "$dir"; tmp="$(mktemp -d)"
curl -fsSL -o "$tmp/inter.zip" https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip
unzip -q -o "$tmp/inter.zip" -d "$tmp/inter"
cp "$tmp/inter/extras/ttf/Inter-Bold.ttf" "$tmp/inter/extras/ttf/Inter-SemiBold.ttf" "$dir/"
curl -fsSL -o "$dir/NotoSansMalayalam-Bold.ttf" \
  https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSansMalayalam/hinted/ttf/NotoSansMalayalam-Bold.ttf
curl -fsSL -o "$dir/NotoSansArabic-Bold.ttf" \
  https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSansArabic/hinted/ttf/NotoSansArabic-Bold.ttf
curl -fsSL -o "$dir/NotoSansCJKsc-Bold.otf" \
  https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf
rm -rf "$tmp"; ls -l "$dir"
