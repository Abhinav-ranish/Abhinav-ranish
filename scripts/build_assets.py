#!/usr/bin/env python3
"""Render every custom SVG on the profile from one token table.

Usage
  python scripts/build_assets.py              # build all assets (uses cached glyph outlines)
  python scripts/build_assets.py --fetch-icons  # (re)download icon sources into assets/src/icons/

CI runs need only the Python standard library. Display text is drawn as
path outlines. Outlines live in assets/src/textpaths.json. When display text
changes, run scripts/fetch_fonts.sh once and install `fonttools uharfbuzz`:
the text is shaped with HarfBuzz (needed for Malayalam conjuncts, vowel signs
and the virama) and the cache is refreshed.

Every SVG obeys the same rules:
  * resting frame == final frame: intros animate FROM a hidden state with
    animation-fill-mode: backwards, SMIL uses fill="freeze" with base
    attributes already at their final value;
  * no <script>, no foreignObject, no external href / @import / web fonts;
  * @media (prefers-reduced-motion: reduce) stops CSS motion;
  * every file is parsed with xml.etree before it is written.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SRC = ASSETS / "src"
FONT_DIR = Path(os.environ.get("FONT_DIR", SRC / "fonts"))
TEXT_CACHE = SRC / "textpaths.json"
ICON_DIR = SRC / "icons"
README = ROOT / "README.md"

# ----------------------------------------------------------------------------
# Tokens: "Phoenix Dusk"
# ----------------------------------------------------------------------------
PAL = {
    "dark": dict(
        bg="#0B0F17", card="#0D1117", raised="#161B22", line="#21262D", line2="#30363D",
        fg="#E6EDF3", mut="#8B949E", accent="#22D3EE", violet="#A78BFA", amber="#F59E0B",
        rose="#F43F5E", ok="#3FB950", grid="#22D3EE", grid_op=".13",
        sun=("#F59E0B", "#F43F5E"), sun_op=".55", glow="#A78BFA", glow_op=".10",
        cactus=("#3FB950", "#238636"), shimmer="#FFFFFF", shimmer_op=".16",
        chip_txt="#0B0F17",
    ),
    "light": dict(
        bg="#FFFFFF", card="#F6F8FA", raised="#FFFFFF", line="#D0D7DE", line2="#AFB8C1",
        fg="#1F2328", mut="#57606A", accent="#1D4ED8", violet="#6D28D9", amber="#B45309",
        rose="#E11D48", ok="#1A7F37", grid="#1D4ED8", grid_op=".10",
        sun=("#FBBF24", "#FB7185"), sun_op=".62", glow="#1D4ED8", glow_op=".06",
        cactus=("#2DA44E", "#1A7F37"), shimmer="#1D4ED8", shimmer_op=".10",
        chip_txt="#FFFFFF",
    ),
}
EASE = "cubic-bezier(.2,.7,.2,1)"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
SANS = "ui-sans-serif,system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"
REDUCED = "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
FONTS = {
    "bold": "Inter-Bold.ttf",
    "semi": "Inter-SemiBold.ttf",
    "ml": "NotoSansMalayalam-Bold.ttf",
}
NAME_ML = "അഭിനവ് റനീഷ്"


def log(*a):
    print("[build_assets]", *a, file=sys.stderr)


# ----------------------------------------------------------------------------
# Text -> shaped path outlines (HarfBuzz + fontTools, cached as JSON)
# ----------------------------------------------------------------------------
class TextEngine:
    def __init__(self):
        self.cache = {"fonts": {}, "runs": {}}
        if TEXT_CACHE.exists():
            self.cache = json.loads(TEXT_CACHE.read_text())
        self.dirty = False
        self.used = set()
        self._hb = {}
        self._tt = {}

    def _load(self, key):
        if key in self._tt:
            return
        try:
            import uharfbuzz as hb  # noqa: F401
            from fontTools.ttLib import TTFont
        except ImportError as e:  # pragma: no cover
            raise SystemExit(
                f"display text changed but {e.name} is missing: pip install fonttools uharfbuzz, "
                "then run scripts/fetch_fonts.sh"
            )
        path = FONT_DIR / FONTS[key]
        if not path.exists():
            raise SystemExit(f"missing font {path}; run scripts/fetch_fonts.sh")
        import uharfbuzz as hb
        blob = hb.Blob.from_file_path(str(path))
        self._hb[key] = hb.Font(hb.Face(blob))
        self._tt[key] = TTFont(str(path))

    def _outline(self, key, gid):
        from fontTools.pens.basePen import BasePen

        class Rec(BasePen):
            def __init__(self, gs):
                super().__init__(gs)
                self.ops = []

            def _moveTo(self, p):
                self.ops.append(["M", *map(round, p)])

            def _lineTo(self, p):
                self.ops.append(["L", *map(round, p)])

            def _curveToOne(self, a, b, c):
                self.ops.append(["C", *map(round, (*a, *b, *c))])

            def _qCurveToOne(self, a, b):
                self.ops.append(["Q", *map(round, (*a, *b))])

            def _closePath(self):
                self.ops.append(["Z"])

        tt = self._tt[key]
        gs = tt.getGlyphSet()
        pen = Rec(gs)
        gs[tt.getGlyphName(gid)].draw(pen)
        return pen.ops

    def shape(self, key, text):
        rk = f"{key}␟{text}"
        self.used.add(rk)
        run = self.cache["runs"].get(rk)
        if run is None:
            import uharfbuzz as hb
            self._load(key)
            buf = hb.Buffer()
            buf.add_str(text)
            buf.guess_segment_properties()
            hb.shape(self._hb[key], buf, {"kern": True, "liga": True})
            x = 0
            glyphs = []
            fc = self.cache["fonts"].setdefault(
                key, {"upem": self._tt[key]["head"].unitsPerEm, "glyphs": {}}
            )
            for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
                gid = info.codepoint
                glyphs.append([gid, x + pos.x_offset, pos.y_offset])
                if str(gid) not in fc["glyphs"]:
                    fc["glyphs"][str(gid)] = self._outline(key, gid)
                x += pos.x_advance
            run = {"g": glyphs, "adv": x}
            self.cache["runs"][rk] = run
            self.dirty = True
        return run

    def glyph_paths(self, key, text, size, x, y, anchor="start", max_w=None):
        """Return ([(d, cx), ...], width, size). Glyph coords are baked in, so CSS
        transforms on a glyph never fight a transform attribute."""
        run = self.shape(key, text)
        fc = self.cache["fonts"][key]
        upem = fc["upem"]
        s = size / upem
        w = run["adv"] * s
        if max_w and w > max_w:
            size *= max_w / w
            s = size / upem
            w = run["adv"] * s
        ox = x - (w / 2 if anchor == "middle" else w if anchor == "end" else 0)
        out = []
        for gid, gx, gy in run["g"]:
            ops = fc["glyphs"][str(gid)]
            if not ops:
                continue
            d = ops_to_d(ops, ox + gx * s, y - gy * s, s)
            out.append((d, ox + gx * s))
        return out, w, size

    def save(self):
        # drop runs (and glyphs) not used this build so the cache tracks the live text
        runs = {k: v for k, v in self.cache["runs"].items() if k in self.used}
        if runs.keys() != self.cache["runs"].keys():
            self.dirty = True
        if self.dirty:
            fonts = {}
            for rk, run in runs.items():
                key = rk.split("␟", 1)[0]
                src = self.cache["fonts"][key]
                f = fonts.setdefault(key, {"upem": src["upem"], "glyphs": {}})
                for gid, _, _ in run["g"]:
                    f["glyphs"][str(gid)] = src["glyphs"][str(gid)]
            self.cache = {"fonts": fonts, "runs": runs}
            TEXT_CACHE.write_text(json.dumps(self.cache, separators=(",", ":"), ensure_ascii=False))
            log("updated", TEXT_CACHE.relative_to(ROOT))


def f1(v):
    s = f"{v:.1f}"
    return s[:-2] if s.endswith(".0") else ("-0" if s == "-0.0" else s)


def ops_to_d(ops, ox, oy, s):
    parts = []
    for op in ops:
        c, pts = op[0], op[1:]
        if c == "Z":
            parts.append("Z")
            continue
        co = []
        for i in range(0, len(pts), 2):
            co.append(f1(ox + pts[i] * s))
            co.append(f1(oy - pts[i + 1] * s))
        parts.append(c + " ".join(co))
    return "".join(parts)


TE = TextEngine()


def ptext(key, text, size, x, y, fill, anchor="start", max_w=None, cls=None, glyph_cls=None):
    """Path text. With glyph_cls, every glyph gets its own element (for staggers)."""
    glyphs, w, size = TE.glyph_paths(key, text, size, x, y, anchor, max_w)
    c = f' class="{cls}"' if cls else ""
    if glyph_cls:
        inner = "".join(f'<path class="{glyph_cls} {glyph_cls}{i}" d="{d}"/>' for i, (d, _) in enumerate(glyphs))
        return f'<g fill="{fill}"{c}>{inner}</g>', w, len(glyphs)
    return f'<path{c} fill="{fill}" d="{"".join(d for d, _ in glyphs)}"/>', w, len(glyphs)


def mtext(s, x, y, size, fill, anchor="start", weight=400, cls=None, family=MONO, extra=""):
    c = f' class="{cls}"' if cls else ""
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    return (f'<text{c} x="{f1(x)}" y="{f1(y)}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}"{a}{extra}>{escape(s)}</text>')


def svg_doc(w, h, title, desc, css, body, defs=""):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'role="img" aria-labelledby="t d" shape-rendering="geometricPrecision" '
        f'text-rendering="geometricPrecision">'
        f'<title id="t">{escape(title)}</title><desc id="d">{escape(desc)}</desc>'
        f'<style>{css}{REDUCED}</style>'
        f'<defs>{defs}</defs>{body}</svg>'
    )


def write(name, content):
    ET.fromstring(content)  # fail fast on a stray & or <
    path = ASSETS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def rr_perimeter(w, h, r):
    return 2 * (w + h) - 8 * r + 2 * math.pi * r


# ----------------------------------------------------------------------------
# MASCOT: a saguaro that sprouts on the horizon, then waves and blinks on a loop
# ----------------------------------------------------------------------------
def cactus(p, x, base, s, delay, name="cac"):
    """Saguaro with its base on y=base, ~125*s tall. Returns (css, svg); the svg
    belongs inside a clip at the horizon so the sprout rises out of the ground."""
    green, dark = p["cactus"]
    ink = "#0D1117"
    X = lambda v: f1(x + v * s)
    Y = lambda v: f1(base - v * s)
    # CSS only (looping SMIL is banned); arm pivots on its bbox bottom-left = shoulder
    css = (f".{name}{{animation:sprout .9s {EASE} {delay:.2f}s backwards}}"
           f"@keyframes sprout{{from{{transform:translateY({f1(135 * s)}px)}}}}"
           f".{name}-arm{{transform-box:fill-box;transform-origin:0% 100%;"
           f"animation:{name}-wave 5s ease-in-out {delay + .9:.2f}s infinite}}"
           f"@keyframes {name}-wave{{0%,40%,100%{{transform:rotate(0)}}8%,24%{{transform:rotate(22deg)}}"
           f"16%,32%{{transform:rotate(-8deg)}}}}"
           f".{name}-eye{{transform-box:fill-box;transform-origin:center;animation:{name}-blink 4.2s {delay + 1.4:.2f}s infinite}}"
           f"@keyframes {name}-blink{{0%,92%,100%{{transform:scaleY(1)}}95%{{transform:scaleY(.1)}}}}")
    arm = f'stroke="{green}" stroke-width="{f1(18 * s)}" stroke-linecap="round" stroke-linejoin="round" fill="none"'
    ribs = "".join(f'<line x1="{X(dx)}" y1="{Y(106)}" x2="{X(dx)}" y2="{Y(8)}"/>' for dx in (-9, 9))
    spines = "".join(f'<path d="M{X(dx)} {Y(dy)}l{f1(4 * s * (1 if dx > 0 else -1))} {f1(-3 * s)}"/>'
                     for dx, dy in ((-20, 30), (20, 44), (-20, 74), (20, 20)))
    petals = "".join(f'<circle cx="{X(4.5 * math.cos(a))}" cy="{Y(124 + 4.5 * math.sin(a))}" r="{f1(3.6 * s)}"/>'
                     for a in (k * 2 * math.pi / 5 + math.pi / 2 for k in range(5)))
    svg = (f'<g class="{name}">'
           f'<path d="M{X(-16)} {Y(54)}H{X(-38)}V{Y(80)}" {arm}/>'
           f'<path class="{name}-arm" d="M{X(16)} {Y(62)}H{X(38)}V{Y(94)}" {arm}/>'
           f'<rect x="{X(-20)}" y="{Y(118)}" width="{f1(40 * s)}" height="{f1(130 * s)}" rx="{f1(20 * s)}" fill="{green}"/>'
           f'<g stroke="{dark}" stroke-width="{f1(2 * s)}" stroke-linecap="round" opacity=".6">{ribs}{spines}</g>'
           f'<g fill="{p["rose"]}">{petals}</g><circle cx="{X(0)}" cy="{Y(124)}" r="{f1(2.6 * s)}" fill="{p["amber"]}"/>'
           f'<g class="{name}-eye" fill="{ink}"><ellipse cx="{X(-8)}" cy="{Y(92)}" rx="{f1(3.2 * s)}" ry="{f1(4.4 * s)}"/>'
           f'<ellipse cx="{X(8)}" cy="{Y(92)}" rx="{f1(3.2 * s)}" ry="{f1(4.4 * s)}"/></g>'
           f'<g fill="{p["rose"]}" opacity=".55"><circle cx="{X(-13)}" cy="{Y(84)}" r="{f1(3 * s)}"/>'
           f'<circle cx="{X(13)}" cy="{Y(84)}" r="{f1(3 * s)}"/></g>'
           f'<path d="M{X(-6)} {Y(84)}q{f1(6 * s)} {f1(6 * s)} {f1(12 * s)} 0" stroke="{ink}" stroke-width="{f1(2.4 * s)}" '
           f'stroke-linecap="round" fill="none"/></g>')
    return css, svg


# ----------------------------------------------------------------------------
# HERO: cold open
# ----------------------------------------------------------------------------
HERO_ROLE = "CVfy · Focus Spoofer · security & AI"


def hero(theme):
    p = PAL[theme]
    # Layout rule: nothing meant to be read is under ~34 units in this
    # 1200-wide viewBox (about 10px on a 360px phone).
    W, H, HZ, SUN_R = 1200, 460, 350, 70
    css = f"""
.ng{{animation:rise .8s {EASE} backwards}}
@keyframes rise{{from{{opacity:0;transform:translateY(18px)}}}}
.ml{{animation:rise .6s {EASE} .75s backwards}}
.floor{{animation:fade 1.2s ease .5s backwards}}
@keyframes fade{{from{{opacity:0}}}}
.hz{{animation:draw .9s {EASE} .8s backwards}}
@keyframes draw{{from{{stroke-dashoffset:600}}}}
.sun{{animation:sunrise 1.4s {EASE} .9s backwards}}
@keyframes sunrise{{from{{transform:translateY({SUN_R + 20}px)}}}}
.role{{animation:rise .7s {EASE} 1.3s backwards}}
.cur{{animation:blink 1.1s steps(1) 2s 3}}
@keyframes blink{{50%{{opacity:0}}}}
"""
    # solid name: the sunset carries the colour, the type stays quiet
    name, nw, ng = ptext("bold", "Abhinav Ranish", 132, W / 2, 182, p["fg"], "middle", max_w=900, glyph_cls="ng")
    stagger = "".join(f".ng{i}{{animation-delay:{.035 * i:.3f}s}}" for i in range(ng))
    # Malayalam sits clear of the sun (sun top = HZ - SUN_R = 280)
    ml, _, _ = ptext("ml", NAME_ML, 46, W / 2, 250, p["mut"], "middle", cls="ml")
    role, rw, _ = ptext("semi", HERO_ROLE, 36, W / 2 - 14, 424, p["fg"], "middle", max_w=1040)
    s1, s2 = p["sun"]
    defs = (
        f'<radialGradient id="sg" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="{s1}"/>'
        f'<stop offset=".62" stop-color="{s2}"/><stop offset="1" stop-color="{s2}" stop-opacity="0"/></radialGradient>'
        f'<clipPath id="frame"><rect width="{W}" height="{H}" rx="24"/></clipPath>'
        f'<clipPath id="sky"><rect width="{W}" height="{HZ}"/></clipPath>'
        f'<mask id="scan"><rect width="{W}" height="{H}" fill="#fff"/>'
        + "".join(f'<rect y="{HZ - 12 - k * 14}" width="{W}" height="{2 + k * .2:.1f}" fill="#000"/>' for k in range(4))
        + "</mask>"
    )
    vx = W / 2
    floor = [f'<line x1="{vx}" y1="{HZ}" x2="{vx + k * 260}" y2="{H}"/>' for k in range(-4, 5)]
    floor += [f'<line x1="0" y1="{f1(HZ + d)}" x2="{W}" y2="{f1(HZ + d)}"/>' for d in (8, 20, 37, 60, 90)]
    cx = W / 2 - 14 + rw / 2 + 12  # cursor sits right after the measured role outline
    # the saguaro sprouts right of the sun once it has risen; its top (~225) clears the name
    cac_css, cac = cactus(p, 960, HZ, 1.0, 2.2)
    css += cac_css
    body = (
        f'<g clip-path="url(#frame)">'
        f'<rect width="{W}" height="{H}" fill="{p["bg"]}"/>'
        # The clip stays put on the outer group; only the inner group moves.
        f'<g clip-path="url(#sky)"><g class="sun"><circle cx="{W / 2}" cy="{HZ}" r="{SUN_R}" fill="url(#sg)" '
        f'opacity="{p["sun_op"]}" mask="url(#scan)"/></g></g>'
        f'<g class="floor" stroke="{p["grid"]}" stroke-opacity="{p["grid_op"]}" stroke-width="1.2">{"".join(floor)}</g>'
        f'{name}{ml}'
        f'<path class="hz" d="M{W / 2} {HZ}H{W - 40}" stroke="{p["line2"]}" stroke-width="1.5" stroke-dasharray="600"/>'
        f'<path class="hz" d="M{W / 2} {HZ}H40" stroke="{p["line2"]}" stroke-width="1.5" stroke-dasharray="600"/>'
        f'<g clip-path="url(#sky)">{cac}</g>'
        f'<g class="role">{role}<rect class="cur" x="{f1(cx)}" y="396" width="16" height="34" fill="{p["accent"]}"/></g>'
        f'</g><rect x=".75" y=".75" width="{W - 1.5}" height="{H - 1.5}" rx="23.5" fill="none" stroke="{p["line"]}" stroke-width="1.5"/>'
    )
    return svg_doc(W, H, "Abhinav Ranish (അഭിനവ് റനീഷ്)",
                   "Builds CVfy and Focus Spoofer. Software, security and AI, built end to end.",
                   css + stagger, body, defs)


# ----------------------------------------------------------------------------
# CTA BUTTONS
# ----------------------------------------------------------------------------
def simple_icon(name):
    raw = (ICON_DIR / "simple" / f"{name}.svg").read_text()
    return re.search(r'<path d="([^"]+)"', raw).group(1)


BTN_ICONS = {
    "site": lambda c: (f'<g fill="none" stroke="{c}" stroke-width="1.8"><circle cx="12" cy="12" r="10"/>'
                       f'<path d="M2 12h20M12 2c3 3 4.5 6.5 4.5 10S15 19 12 22M12 2C9 5 7.5 8.5 7.5 12S9 19 12 22"/></g>'),
    "hire": lambda c: (f'<g fill="none" stroke="{c}" stroke-width="1.8" stroke-linejoin="round">'
                       f'<path d="M6 2h8l5 5v15H6z"/><path d="M14 2v5h5M9 12h7M9 16h7"/></g>'),
    "email": lambda c: (f'<g fill="none" stroke="{c}" stroke-width="1.8" stroke-linejoin="round">'
                        f'<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M3 6l9 7 9-7"/></g>'),
}
# (slug, label or None for icon-only, simple-icons name, primary)
BUTTONS = [
    ("hire", "hiring? start here", None, True),
    ("site", "aranish.uk", None, False),
    ("linkedin", "LinkedIn", "linkedin", False),
    ("email", "Email", None, False),
    ("x", None, "x", False),
]


def button(theme, idx, slug, label, si, primary=False):
    p = PAL[theme]
    H, PADL, ICON, GAP, PADR = 48, 18, 20, 10, 20
    fg = p["chip_txt"] if primary else p["fg"]
    if label:
        lab, lw, _ = ptext("semi", label, 19, PADL + ICON + GAP, 31, fg)
        W = round(PADL + ICON + GAP + lw + PADR)
        ix = PADL
    else:  # icon-only square pill
        lab, W = "", H
        ix = (H - ICON) / 2
    if si:
        icon = f'<path fill="{fg}" transform="translate({f1(ix)} 14) scale(.8333)" d="{simple_icon(si)}"/>'
    else:
        icon = f'<g transform="translate({f1(ix)} 14) scale(.8333)">{BTN_ICONS[slug](fg)}</g>'
    delay = 1.2 + .12 * idx
    css = f".sh{{animation:sweep .9s ease-in-out {delay:.2f}s backwards}}@keyframes sweep{{from{{transform:translateX(-80px)}}}}"
    shim = "#FFFFFF" if primary else p["shimmer"]
    shim_op = ".35" if primary else p["shimmer_op"]
    fill, stroke = (p["accent"], p["accent"]) if primary else (p["raised"], p["line2"])
    defs = (f'<clipPath id="pill"><rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="23"/></clipPath>'
            f'<linearGradient id="shg" x1="0" x2="1"><stop offset="0" stop-color="{shim}" stop-opacity="0"/>'
            f'<stop offset=".5" stop-color="{shim}" stop-opacity="{shim_op}"/>'
            f'<stop offset="1" stop-color="{shim}" stop-opacity="0"/></linearGradient>')
    body = (f'<rect x=".75" y=".75" width="{W - 1.5}" height="{H - 1.5}" rx="23.25" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="1.5"/>{icon}{lab}'
            # shimmer rests past the right edge (decoration only)
            f'<g clip-path="url(#pill)"><rect class="sh" x="{W + 20}" y="-10" width="60" height="{H + 20}" '
            f'fill="url(#shg)" transform="skewX(-20)"/></g>')
    title = label or {"x": "X @madebyabhinav"}.get(slug, slug)
    return svg_doc(W, H, title, f"Button: {title}", css, body, defs)


# ----------------------------------------------------------------------------
# PRODUCT CARDS
# ----------------------------------------------------------------------------
CW, CH = 1000, 320
# Card copy. Claims checked against the live sites on 2026-10-25; the SVG
# subtitle and the README alt text are built from the same strings.
FOCUS = dict(title="Focus Spoofer", hook="Switch tabs. The page never knows.", sub="Chrome extension · open source",
             desc="Spoofs tab visibility and focus so sites can't tell you left, and randomizes your canvas, WebGL and audio fingerprint.")
CVFY = dict(title="CVfy", hook="Your resume, already a website.", sub="PDF or DOCX → live site in under a minute",
            desc="Upload the resume you already have and get a themed site you can edit and publish.")


def card_frame(p, offset, glow_x=180):
    per = rr_perimeter(CW - 3, CH - 3, 22)
    css = (f".edge{{stroke-dasharray:{per:.0f};animation:edge 1.6s {EASE} {offset:.2f}s backwards}}"
           f"@keyframes edge{{from{{stroke-dashoffset:{per:.0f}}}}}"
           f".rise{{animation:rise .7s {EASE} backwards}}@keyframes rise{{from{{opacity:0;transform:translateY(12px)}}}}"
           f".live{{animation:pulse 2.2s ease-in-out {offset + 2.6:.2f}s 3}}@keyframes pulse{{50%{{opacity:.3}}}}"
           f"@keyframes fade{{from{{opacity:0}}}}")
    defs = (f'<radialGradient id="glow" cx="{glow_x / CW:.2f}" cy=".5" r=".55"><stop offset="0" stop-color="{p["glow"]}" '
            f'stop-opacity="{p["glow_op"]}"/><stop offset="1" stop-color="{p["glow"]}" stop-opacity="0"/></radialGradient>'
            f'<clipPath id="cardclip"><rect x="1.5" y="1.5" width="{CW - 3}" height="{CH - 3}" rx="22"/></clipPath>')
    back = (f'<rect x="1.5" y="1.5" width="{CW - 3}" height="{CH - 3}" rx="22" fill="{p["card"]}" stroke="{p["line"]}"/>'
            f'<rect x="1.5" y="1.5" width="{CW - 3}" height="{CH - 3}" rx="22" fill="url(#glow)"/>')
    edge = (f'<rect class="edge" x="1.5" y="1.5" width="{CW - 3}" height="{CH - 3}" rx="22" fill="none" '
            f'stroke="{p["line2"]}" stroke-width="2"/>')
    return css, defs, back, edge


def status_pill(p, label, offset):
    lab, lw, _ = ptext("semi", label, 26, 0, 0, p["fg"])  # measure first
    w = lw + 66
    x = CW - 34 - w
    lab, _, _ = ptext("semi", label, 26, x + 44, 61, p["fg"])
    return (f'<g class="rise" style="animation-delay:{offset + .2:.2f}s">'
            f'<rect x="{f1(x)}" y="28" width="{f1(w)}" height="44" rx="22" fill="{p["raised"]}" stroke="{p["line2"]}"/>'
            f'<circle class="live" cx="{f1(x + 24)}" cy="50" r="7" fill="{p["ok"]}"/>{lab}</g>')


def card_text(p, offset, c):
    # Mobile rule: no copy under 28 units in this 1000-wide card.
    X, MAXW = 380, 590
    t, _, _ = ptext("bold", c["title"], 76, X, 130, p["fg"], max_w=MAXW)
    h, _, _ = ptext("semi", c["hook"], 40, X, 192, p["fg"], max_w=MAXW)
    s, _, _ = ptext("semi", c["sub"], 30, X, 246, p["mut"], max_w=MAXW)
    return (f'<g class="rise" style="animation-delay:{offset + .3:.2f}s">{t}</g>'
            f'<g class="rise" style="animation-delay:{offset + .45:.2f}s">{h}</g>'
            f'<g class="rise" style="animation-delay:{offset + .6:.2f}s">{s}</g>')


def card_alt(c):
    return f"{c['title']}: {c['hook'][0].lower() + c['hook'][1:]} {c['desc']} {c['sub'].replace('→', 'to')}."


def card_focus(theme):
    p = PAL[theme]
    o = 0.35  # plays after the CVfy card
    css, defs, back, edge = card_frame(p, o)
    gx, gy = 185, 96
    # page-side signals fly at the eye and get swallowed; the eye stays open
    chips_def = [("blur", p["accent"], -46), ("hidden", p["violet"], 0), ("canvas fp", p["amber"], 46)]
    css += ("".join(f"@keyframes fly{i}{{0%{{opacity:1;transform:translate(-120px,{dy}px) scale(1)}}80%{{opacity:1}}"
                    f"100%{{opacity:0;transform:translate(0,0) scale(.55)}}}}" for i, (_, _, dy) in enumerate(chips_def))
            + f".chip{{opacity:0}}"
            f".pop{{transform-box:fill-box;transform-origin:center;animation:pop .45s {EASE} backwards}}"
            "@keyframes pop{from{opacity:0;transform:scale(.85)}}"
            ".tap{animation:tap .5s ease 1.25s backwards}@keyframes tap{0%{transform:translateY(0)}50%{transform:translateY(-6px)}}")
    glyph = (f'<g class="tap" fill="none" stroke="{p["fg"]}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">'
             f'<path d="M{gx - 52} {gy}q52-50 104 0q-52 50-104 0z"/><circle cx="{gx}" cy="{gy}" r="16"/>'
             f'<circle cx="{gx}" cy="{gy}" r="4" fill="{p["fg"]}"/></g>')
    chips = []
    for i, (lab, col, _) in enumerate(chips_def):
        w = len(lab) * 11 + 26
        chips.append(f'<g class="chip" style="animation:fly{i} .75s {EASE} {o + .2 + .25 * i:.2f}s backwards">'
                     f'<rect x="{f1(gx - w / 2)}" y="{gy - 2}" width="{f1(w)}" height="30" rx="15" fill="{col}"/>'
                     + mtext(lab, gx, gy + 19, 17, p["chip_txt"], "middle", 700) + "</g>")
    pills = []
    for i, (lab, col) in enumerate([("hidden → false", p["ok"]), ("hasFocus() → true", p["accent"]), ("fingerprint ~ random", p["violet"])]):
        y = 178 + i * 42
        pills.append(f'<g class="pop" style="animation-delay:{o + 1.5 + .08 * i:.2f}s">'
                     f'<rect x="60" y="{y}" width="250" height="32" rx="9" fill="{p["raised"]}" stroke="{p["line2"]}"/>'
                     f'<circle cx="80" cy="{y + 16}" r="5" fill="{col}"/>'
                     + mtext(lab, 96, y + 22, 17, p["fg"], weight=600) + "</g>")
    body = (back + glyph + f'<g clip-path="url(#cardclip)">{"".join(chips)}</g>' + "".join(pills)
            + card_text(p, o, FOCUS) + status_pill(p, "focus.oddworks.us", o) + edge)
    return svg_doc(CW, CH, "Focus Spoofer: switch tabs, the page never knows", card_alt(FOCUS), css, body, defs)


def card_cvfy(theme):
    p = PAL[theme]
    o = 0.0
    css, defs, back, edge = card_frame(p, o)
    sw = [p["accent"], p["violet"], p["amber"], p["ok"]]
    css += (f".pdf{{opacity:0;transform-box:fill-box;transform-origin:center;animation:pdf 1.1s {EASE} {o + .1:.2f}s backwards}}"
            "@keyframes pdf{0%,45%{opacity:1;transform:scale(1)}100%{opacity:0;transform:scale(.7)}}"
            f".win{{transform-box:fill-box;transform-origin:center;animation:win .7s {EASE} {o + .75:.2f}s backwards}}"
            "@keyframes win{from{opacity:0;transform:scale(.7,1.15)}}"
            f".blk{{animation:rise .5s {EASE} backwards}}"
            f".acc{{animation:swatch .72s steps(1) {o + 1.6:.2f}s backwards}}"
            f"@keyframes swatch{{0%{{fill:{sw[0]}}}25%{{fill:{sw[1]}}}50%{{fill:{sw[2]}}}75%{{fill:{sw[3]}}}}}"
            f".url{{transform-box:fill-box;transform-origin:0 50%;animation:url .9s steps(14) {o + 1.2:.2f}s backwards}}"
            "@keyframes url{from{transform:scaleX(0)}}")
    defs += '<clipPath id="uc"><rect class="url" x="112" y="62" width="150" height="24"/></clipPath>'
    # portrait "resume.pdf" (intro only) -> landscape browser window (resting frame)
    bars = "".join(f'<rect x="138" y="{70 + k * 26}" width="{84 - (k % 3) * 16}" height="9" rx="4" fill="{p["mut"]}" opacity=".5"/>'
                   for k in range(7))
    pdf = (f'<g class="pdf"><rect x="120" y="40" width="120" height="230" rx="8" fill="{p["raised"]}" stroke="{p["line2"]}" stroke-width="2"/>'
           f'{bars}{mtext("resume.pdf", 180, 292, 15, p["mut"], "middle")}</g>')
    blk = lambda i, s: f'<g class="blk" style="animation-delay:{o + .95 + .06 * i:.2f}s">{s}</g>'
    win = (f'<g class="win"><rect x="50" y="40" width="270" height="240" rx="12" fill="{p["raised"]}" stroke="{p["line2"]}" stroke-width="2"/>'
           f'<path d="M50 52a12 12 0 0 1 12-12h246a12 12 0 0 1 12 12v42H50z" fill="{p["card"]}"/>'
           f'<circle cx="70" cy="58" r="5" fill="#F85149"/><circle cx="86" cy="58" r="5" fill="#D29922"/><circle cx="102" cy="58" r="5" fill="#3FB950"/>'
           f'<rect x="64" y="70" width="242" height="18" rx="9" fill="{p["bg"]}" stroke="{p["line"]}"/>'
           f'<g clip-path="url(#uc)">{mtext("cvfy.dev/u/you", 116, 84, 14, p["mut"])}</g>'
           + blk(0, f'<rect class="acc" x="64" y="106" width="242" height="54" rx="8" fill="{sw[0]}" opacity=".9"/>')
           + blk(1, f'<circle cx="96" cy="133" r="16" fill="{p["card"]}"/>'
                    f'<rect x="122" y="124" width="110" height="9" rx="4" fill="{p["card"]}"/><rect x="122" y="138" width="70" height="7" rx="3" fill="{p["card"]}" opacity=".8"/>')
           + "".join(blk(2 + k, f'<rect x="{64 + k * 83}" y="172" width="76" height="52" rx="7" fill="{p["card"]}" stroke="{p["line"]}"/>'
                               f'<rect x="{72 + k * 83}" y="182" width="44" height="7" rx="3" fill="{p["mut"]}" opacity=".55"/>'
                               f'<rect x="{72 + k * 83}" y="196" width="58" height="6" rx="3" fill="{p["mut"]}" opacity=".35"/>') for k in range(3))
           + blk(5, f'<rect x="64" y="236" width="150" height="8" rx="4" fill="{p["mut"]}" opacity=".35"/>'
                    f'<rect x="64" y="252" width="110" height="8" rx="4" fill="{p["mut"]}" opacity=".25"/>')
           + "</g>")
    body = (back + win + pdf + card_text(p, o, CVFY) + status_pill(p, "cvfy.dev", o) + edge)
    return svg_doc(CW, CH, "CVfy: your resume, already a website", card_alt(CVFY), css, body, defs)


# ----------------------------------------------------------------------------
# STACK: icon grid, icons fetched once at build time and inlined with
# per-icon id prefixes (so gradients/clipPaths never collide)
# ----------------------------------------------------------------------------
STACK = [  # at most 10 per row
    ("BUILD", ["ts", "react", "nextjs", "tailwind", "vite", "nodejs", "swift", "tauri", "go"]),
    ("DATA", ["py", "postgres", "go:neon", "mongodb", "prisma", "go:opentelemetry"]),
    ("AI", ["lobe:claude", "lobe:codex", "t3", "lobe:grok", "lobe:grokbot", "lobe:dotsstudio", "lobe:muse",
            "lobe:kimi", "lobe:qwen", "lobe:opencode"]),
    ("CLOUD", ["aws", "azure", "cloudflare", "vercel", "docker", "githubactions", "git", "linux", "bash"]),
    ("SEC & QA", ["go:playwright", "selenium", "go:burpsuite", "go:wireshark", "go:kali"]),
]
STACK_NAMES = {
    "ts": "TypeScript", "react": "React", "nextjs": "Next.js", "tailwind": "Tailwind CSS", "vite": "Vite",
    "nodejs": "Node.js", "swift": "Swift", "tauri": "Tauri", "go": "Go",
    "py": "Python", "postgres": "PostgreSQL", "go:neon": "Neon", "mongodb": "MongoDB", "prisma": "Prisma",
    "go:opentelemetry": "OpenTelemetry",
    "lobe:claude": "Claude", "lobe:codex": "Codex", "t3": "T3 Code", "lobe:grok": "Grok", "lobe:grokbot": "Grokbot",
    "lobe:dotsstudio": "dots", "lobe:muse": "Muse", "lobe:kimi": "Kimi", "lobe:qwen": "Qwen", "lobe:opencode": "OpenCode",
    "aws": "AWS", "azure": "Azure", "cloudflare": "Cloudflare", "vercel": "Vercel", "docker": "Docker",
    "githubactions": "GitHub Actions", "git": "Git", "linux": "Linux", "bash": "Bash",
    "go:playwright": "Playwright", "selenium": "Selenium", "go:burpsuite": "Burp Suite",
    "go:wireshark": "Wireshark", "go:kali": "Kali Linux",
}
# AI marks come from lobe-icons (24x24, MIT) and sit on a skillicons-style tile;
# these have a brand-colour variant, the rest are monochrome in the theme's ink.
LOBE = "https://unpkg.com/@lobehub/icons-static-svg@1.99.0/icons"
LOBE_COLOR = {"claude", "codex", "kimi", "qwen", "muse"}
TILE = {"dark": ("#242938", "#FFFFFF"), "light": ("#F4F2ED", "#000000")}  # (tile, ink), as skillicons
# T3 Code mark, from pingdotgg/t3code apps/mobile/assets/widget/T3Mark.svg
T3_MARK = ("7 29 111 74", "M33.4509 93V47.56H15.5309V37H64.3309V47.56H46.4109V93H33.4509ZM86.7253 93.96C82.832 93.96 "
           "78.9653 93.4533 75.1253 92.44C71.2853 91.3733 68.032 89.88 65.3653 87.96L70.4053 78.04C72.5386 79.5867 "
           "75.0186 80.8133 77.8453 81.72C80.672 82.6267 83.5253 83.08 86.4053 83.08C89.6586 83.08 92.2186 82.44 "
           "94.0853 81.16C95.952 79.88 96.8853 78.12 96.8853 75.88C96.8853 73.7467 96.0586 72.0667 94.4053 70.84C92.752 "
           "69.6133 90.0853 69 86.4053 69H80.4853V60.44L96.0853 42.76L97.5253 47.4H68.1653V37H107.365V45.4L91.8453 "
           "63.08L85.2853 59.32H89.0453C95.9253 59.32 101.125 60.8667 104.645 63.96C108.165 67.0533 109.925 71.0267 "
           "109.925 75.88C109.925 79.0267 109.099 81.9867 107.445 84.76C105.792 87.48 103.259 89.6933 99.8453 "
           "91.4C96.432 93.1067 92.0586 93.96 86.7253 93.96Z")


def icon_file(theme, ident):
    return ICON_DIR / theme / (ident.replace(":", "-") + ".svg")


def tile(theme, viewbox, inner, fill_rule="nonzero"):
    # same wrapper the icon services use, so inline_icon treats every tile alike
    bg, ink = TILE[theme]
    return ('<svg width="48" height="48" viewBox="0 0 256 256" fill="none" xmlns="http://www.w3.org/2000/svg">'
            '<g transform="translate(0, 0)"><svg width="256" height="256" fill="none" viewBox="0 0 256 256">'
            f'<rect width="256" height="256" fill="{bg}" rx="60"/>'
            f'<svg x="56" y="56" width="144" height="144" viewBox="{viewbox}" fill="{ink}" color="{ink}" '
            f'fill-rule="{fill_rule}">{inner}</svg>'
            '</svg></g></svg>')


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "profile-build-assets"})
    return urllib.request.urlopen(req, timeout=30).read()


def fetch_icons():
    for theme in ("dark", "light"):
        for _, ids in STACK:
            for ident in ids:
                if ident == "t3":
                    raw = tile(theme, T3_MARK[0], f'<path d="{T3_MARK[1]}"/>').encode()
                elif ident.startswith("lobe:"):
                    name = ident[5:] + ("-color" if ident[5:] in LOBE_COLOR else "")
                    src = get(f"{LOBE}/{name}.svg").decode()
                    m = re.search(r'(<svg[^>]*viewBox="([^"]+)"[^>]*>)(.*)</svg>', src, re.S)
                    if not m:
                        raise SystemExit(f"icon {ident}: unexpected markup")
                    rule = "evenodd" if 'fill-rule="evenodd"' in m.group(1) else "nonzero"
                    raw = tile(theme, m.group(2), re.sub(r"<title>.*?</title>", "", m.group(3)), rule).encode()
                elif ident.startswith("go:"):
                    raw = get(f"https://go-skill-icons.vercel.app/api/icons?i={ident[3:]}&theme={theme}")
                else:
                    raw = get(f"https://skillicons.dev/icons?i={ident}&theme={theme}")
                if len(raw) < 300:
                    raise SystemExit(f"icon {ident} ({theme}) came back empty ({len(raw)} bytes): unknown id?")
                f = icon_file(theme, ident)
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(raw)
                log("icon", f.relative_to(ROOT), len(raw))


def inline_icon(theme, ident, prefix):
    raw = icon_file(theme, ident).read_text()
    if len(raw) < 300:
        raise SystemExit(f"icon {ident} is empty")
    # The service wraps the icon: <svg 48x48><g transform><svg 256x256>...</svg></g></svg>
    g = raw.find("<g transform")
    m = re.search(r"<svg[^>]*>(.*)</svg>\s*</g>", raw[g:], re.S) if g >= 0 else None
    if not m:
        raise SystemExit(f"icon {ident}: unexpected markup")
    inner = m.group(1)
    if "<script" in inner or "foreignObject" in inner or re.search(r'href="https?:', inner):
        raise SystemExit(f"icon {ident} contains forbidden content")
    ids = set(re.findall(r'\bid="([^"]+)"', inner))
    for i in sorted(ids, key=len, reverse=True):
        inner = re.sub(rf'\bid="{re.escape(i)}"', f'id="{prefix}{i}"', inner)
        inner = inner.replace(f"url(#{i})", f"url(#{prefix}{i})")
        inner = re.sub(rf'href="#{re.escape(i)}"', f'href="#{prefix}{i}"', inner)
    return inner


def stack(theme):
    p = PAL[theme]
    # 10 tiles per row: X0 + 10 * (T + GAP) - GAP = 918, inside the 34-unit margin
    T, GAP, X0, PITCH = 60, 12, 210, 88
    W, H = 1000, 34 + len(STACK) * PITCH + 6
    css = [f".ic{{transform-box:fill-box;transform-origin:center;animation:pop .5s {EASE} backwards}}"
           "@keyframes pop{from{opacity:0;transform:translateY(10px) scale(.9)}}"]
    body = [f'<rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="20" fill="{p["card"]}" stroke="{p["line"]}" stroke-width="1.5"/>']
    n = 0
    for r, (label, ids) in enumerate(STACK):
        y = 34 + r * PITCH
        body.append(mtext(label, 34, y + 37, 19, p["mut"], weight=700, extra=' letter-spacing="2"'))
        for c, ident in enumerate(ids):
            x = X0 + c * (T + GAP)
            inner = inline_icon(theme, ident, f"i{n}-")
            css.append(f".ic{n}{{animation-delay:{.035 * n:.3f}s}}")
            body.append(f'<g class="ic ic{n}"><svg x="{x}" y="{y}" width="{T}" height="{T}" viewBox="0 0 256 256" '
                        f'fill="none">{inner}</svg></g>')
            n += 1
        if r < len(STACK) - 1:
            body.append(f'<line x1="34" y1="{y + T + 14}" x2="{W - 34}" y2="{y + T + 14}" stroke="{p["line"]}"/>')
    return svg_doc(W, H, "which tools", stack_alt(), "".join(css), "".join(body))


def stack_alt():
    return ". ".join(f"{lab}: " + ", ".join(STACK_NAMES[i] for i in ids) for lab, ids in STACK) + "."


# ----------------------------------------------------------------------------
# FOOTER: one line + a small sunset on the horizon
# ----------------------------------------------------------------------------
def footer(theme):
    p = PAL[theme]
    W, H, HZ, SUN_R = 800, 164, 124, 28
    s2 = p["sun"][1]
    # measure both runs, then lay them out as one centred line
    _, nw, _ = ptext("bold", "Abhinav Ranish", 36, 0, 0, p["fg"])
    _, pw, _ = ptext("semi", "  ·  Phoenix, AZ", 30, 0, 0, p["mut"])
    x0 = (W - nw - pw) / 2
    name, _, _ = ptext("bold", "Abhinav Ranish", 36, x0, 62, p["fg"])
    place, _, _ = ptext("semi", "  ·  Phoenix, AZ", 30, x0 + nw, 62, p["mut"])
    css = (f".roll{{animation:roll .8s {EASE} backwards}}@keyframes roll{{from{{opacity:0;transform:translateY(16px)}}}}"
           f".sun{{animation:sink 2.4s {EASE} .2s backwards}}@keyframes sink{{from{{transform:translateY(-28px)}}}}")
    # same saguaro as the hero, waving goodbye just right of the name line
    cac_css, cac = cactus(p, min(W - 70, x0 + nw + pw + 70), HZ, .7, .6)
    css += cac_css
    defs = (f'<radialGradient id="sg" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="{s2}"/>'
            f'<stop offset=".6" stop-color="{p["violet"]}"/><stop offset="1" stop-color="{p["violet"]}" stop-opacity="0"/></radialGradient>'
            f'<clipPath id="sky"><rect width="{W}" height="{HZ}"/></clipPath>'
            f'<clipPath id="frame"><rect width="{W}" height="{H}" rx="20"/></clipPath>')
    vx = W / 2
    floor = [f'<line x1="{vx}" y1="{HZ}" x2="{vx + k * 180}" y2="{H}"/>' for k in range(-4, 5)]
    floor += [f'<line x1="0" y1="{HZ + d}" x2="{W}" y2="{HZ + d}"/>' for d in (7, 17, 31)]
    body = (f'<g clip-path="url(#frame)"><rect width="{W}" height="{H}" fill="{p["bg"]}"/>'
            # static clip on the outer group, motion on the inner one; the sun's
            # top (HZ - SUN_R = 96) stays well below the text baseline (62)
            f'<g clip-path="url(#sky)"><g class="sun"><circle cx="{W / 2}" cy="{HZ}" r="{SUN_R}" fill="url(#sg)" opacity="{p["sun_op"]}"/></g></g>'
            f'<g stroke="{p["grid"]}" stroke-opacity="{p["grid_op"]}" stroke-width="1.2">{"".join(floor)}</g>'
            f'<line x1="40" y1="{HZ}" x2="{W - 40}" y2="{HZ}" stroke="{p["line2"]}" stroke-width="1.5"/>'
            f'<g clip-path="url(#sky)">{cac}</g>'
            f'<g class="roll" style="animation-delay:.1s">{name}{place}</g>'
            f'</g><rect x=".75" y=".75" width="{W - 1.5}" height="{H - 1.5}" rx="19.5" fill="none" stroke="{p["line"]}" stroke-width="1.5"/>')
    return svg_doc(W, H, "Abhinav Ranish · Phoenix, AZ", "Abhinav Ranish, Phoenix, AZ.", css, body, defs)


# ----------------------------------------------------------------------------
# README rewriting (alt text == SVG copy)
# ----------------------------------------------------------------------------
def rewrite_readme():
    if not README.exists():
        return
    txt = README.read_text()
    for slug, alt in (("card-focus", card_alt(FOCUS)), ("card-cvfy", card_alt(CVFY)), ("stack", stack_alt())):
        txt = re.sub(rf'(<img alt=")[^"]*(" src="\./assets/{slug}-light\.svg")',
                     lambda m: m.group(1) + escape(alt, {'"': "&quot;"}) + m.group(2), txt)
    README.write_text(txt)


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch-icons", action="store_true")
    a = ap.parse_args()
    if a.fetch_icons:
        fetch_icons()
    out = []
    for th in ("dark", "light"):
        out.append(write(f"hero-{th}.svg", hero(th)))
        for i, (slug, label, si, primary) in enumerate(BUTTONS):
            out.append(write(f"btn-{slug}-{th}.svg", button(th, i, slug, label, si, primary)))
        out.append(write(f"card-focus-{th}.svg", card_focus(th)))
        out.append(write(f"card-cvfy-{th}.svg", card_cvfy(th)))
        out.append(write(f"stack-{th}.svg", stack(th)))
        out.append(write(f"footer-{th}.svg", footer(th)))
    # drop SVGs this build no longer produces, so retired assets don't linger
    for stale in sorted(set(ASSETS.glob("*.svg")) - set(out)):
        log("removing stale", stale.relative_to(ROOT))
        stale.unlink()
    TE.save()
    rewrite_readme()
    total = sum(f.stat().st_size for f in out)
    for f in out:
        log(f"{f.relative_to(ROOT)} {f.stat().st_size / 1024:.1f} KB")
    log(f"{len(out)} files, {total / 1024:.0f} KB total")


if __name__ == "__main__":
    main()
