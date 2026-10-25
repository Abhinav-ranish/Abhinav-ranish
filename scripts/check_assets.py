#!/usr/bin/env python3
"""Validate generated SVGs. Standard library only.

  python scripts/check_assets.py [--static-dir DIR]

Checks, failing the job on any violation:
  * every assets/**/*.svg parses as XML;
  * per-file budget (150 KB) and total budget (1.5 MB at rest);
  * no <script>, foreignObject, @import, external href/url(), web fonts;
  * resting frame == final frame: every SMIL <animate>/<animateTransform>/
    <animateMotion> uses fill="freeze"; no feGaussianBlur that is not at 0;
    no element whose *base* opacity is 0 unless it is intro-only decoration
    (classes listed in DECOR, or a SMIL-driven packet);
With --static-dir, it also writes a copy of every SVG with all motion
removed (CSS animation:none, SMIL elements deleted). CI rasterises those
with rsvg-convert so a reviewer can eyeball exactly what the GitHub mobile
app, thumbnails and reduced-motion users see.
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SVGNS = "{http://www.w3.org/2000/svg}"
PER_FILE = 150 * 1024
TOTAL = 1536 * 1024
DECOR = {"chip", "pdf"}  # intro-only elements that exit the frame by design
FORBIDDEN = [
    (re.compile(r"<script", re.I), "<script>"),
    (re.compile(r"foreignObject"), "foreignObject"),
    (re.compile(r"@import"), "@import"),
    (re.compile(r"@font-face"), "web font"),
    (re.compile(r'href="(?!#)'), "external href"),
    (re.compile(r"url\((?!#)"), "external url()"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--static-dir")
    a = ap.parse_args()
    errors, total = [], 0
    files = sorted(p for p in ASSETS.rglob("*.svg") if "src" not in p.parts)
    if not files:
        sys.exit("no SVGs found")
    for f in files:
        rel = f.relative_to(ROOT)
        raw = f.read_text()
        size = len(raw.encode())
        total += size
        if size > PER_FILE:
            errors.append(f"{rel}: {size // 1024} KB > 150 KB")
        for rx, what in FORBIDDEN:
            if rx.search(raw):
                errors.append(f"{rel}: contains {what}")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            errors.append(f"{rel}: XML error {e}")
            continue
        smil_targets = set()
        for parent in root.iter():
            for el in parent:
                tag = el.tag.replace(SVGNS, "")
                if tag in ("animate", "animateTransform", "animateMotion", "set"):
                    smil_targets.add(id(parent))
                    if el.get("fill") != "freeze":
                        errors.append(f"{rel}: <{tag}> without fill=freeze")
                    if el.get("repeatCount") or el.get("repeatDur"):
                        errors.append(f"{rel}: looping SMIL <{tag}>")
        for el in root.iter():
            tag = el.tag.replace(SVGNS, "")
            if tag == "feGaussianBlur" and el.get("stdDeviation", "0") not in ("0", "0 0"):
                errors.append(f"{rel}: blurred resting frame")
            classes = set((el.get("class") or "").split())
            if el.get("opacity") in ("0", "0.0") and id(el) not in smil_targets and not classes & DECOR:
                errors.append(f"{rel}: <{tag}> has base opacity 0")
        css = "".join(s.text or "" for s in root.iter(SVGNS + "style"))
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"@keyframes[^{]+\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)):
            if re.search(r"(^|;)opacity:0(;|$)", body) and not set(re.findall(r"\.([\w-]+)", sel)) & DECOR:
                errors.append(f"{rel}: CSS rule '{sel.strip()}' hides content at rest")
        if "prefers-reduced-motion" not in css:
            errors.append(f"{rel}: no prefers-reduced-motion block")
        if a.static_dir:
            out = Path(a.static_dir) / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            s = re.sub(r"<(animate|animateTransform|animateMotion|set)\b[^>]*/>", "", raw)
            s = re.sub(r"<animateMotion\b.*?</animateMotion>", "", s, flags=re.S)
            s = s.replace("</style>", "*{animation:none!important}</style>", 1)
            ET.fromstring(s)
            out.write_text(s)
    if total > TOTAL:
        errors.append(f"total {total // 1024} KB > 1.5 MB budget")
    print(f"checked {len(files)} SVGs, {total // 1024} KB total")
    if errors:
        print("\n".join("ERROR " + e for e in errors))
        sys.exit(1)


if __name__ == "__main__":
    main()
