"""
Render a 296×152 pure B&W PNG for the Quote/0 e-ink display.

Claude Code section (always):
  ◆ CLAUDE CODE                 23:08
  5h  [████░░░░░░░░░░░░] 25%  3h12m
  7d  [████████░░░░░░░░] 20%  2.0M

Optional second section (Codex or DeepSeek):
  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
  ◆ CODEX
  5h  [████████████░░░░] 89%  4h41m
  Wk  [████████████░░░░░] 69%  5d23h
"""

from __future__ import annotations

import io
import os
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from claude_usage import format_tokens

W, H = 296, 152
PAD  = 8

BLACK = 0
WHITE = 255

# ── Font loading ──────────────────────────────────────────────────────────────

_HERE        = Path(__file__).parent
_PIXEL_FONT  = _HERE / "assets" / "fonts" / "PixelOperator.ttf"
_VCR_FONT    = _HERE / "assets" / "fonts" / "VCR_OSD_MONO_1.001.ttf"
_TINY_FONT   = _HERE / "assets" / "fonts" / "Minecraftia-Regular.ttf"
_SYS_FONT    = "/System/Library/Fonts/Menlo.ttc"

_fc: dict = {}

def _f(name: str, size: int) -> ImageFont.FreeTypeFont:
    key = (name, size)
    if key not in _fc:
        paths = {
            "pixel": _PIXEL_FONT,
            "vcr":   _VCR_FONT,
            "tiny":  _TINY_FONT,
        }
        try:
            p = paths.get(name)
            _fc[key] = ImageFont.truetype(str(p), size) if p and p.exists() else ImageFont.truetype(_SYS_FONT, size)
        except Exception:
            _fc[key] = ImageFont.truetype(_SYS_FONT, size)
    return _fc[key]


def _sz(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0], bb[3] - bb[1]


# ── Shared drawing primitives ─────────────────────────────────────────────────

def _bar_dots(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, filled_pct: float):
    """Bar: outline + solid fill (left=used) + dot grid in empty area."""
    filled_pct = max(0.0, min(100.0, filled_pct))
    draw.rectangle([x, y, x + w - 1, y + h - 1], outline=BLACK)
    filled = int((w - 2) * filled_pct / 100)
    if filled > 0:
        draw.rectangle([x + 1, y + 1, x + filled, y + h - 2], fill=BLACK)
    step, margin = 4, 2
    for dy in range(y + 1 + margin, y + h - margin, step):
        for dx in range(x + 1 + margin, x + w - margin, step):
            if dx > x + filled:
                draw.point((dx, dy), fill=BLACK)


def _divider(draw: ImageDraw.ImageDraw, y: int) -> int:
    x, dash, gap = 0, 6, 4
    while x < W:
        draw.line([(x, y), (min(x + dash - 1, W - 1), y)], fill=BLACK)
        x += dash + gap
    return y + 10


def _section_label(draw: ImageDraw.ImageDraw, y: int, title: str, font) -> int:
    draw.text((PAD, y), f"◆ {title}", font=font, fill=BLACK)
    _, h = _sz(draw, title, font)
    return y + h + 3


# ── Dual-row renderer (used for both Claude Code and Codex) ───────────────────

ROW_H  = 20   # height of each data row
BAR_H  = 13   # bar height inside row
LBL_W  = 34   # fixed-width column for row label ("5h", "7d", "Wk")


def _dual_row(draw: ImageDraw.ImageDraw, y: int, font,
              row1_label: str, row1_pct: int | None, row1_note: str,
              row2_label: str, row2_pct: int | None, row2_note: str) -> int:
    """
    Draw two bars with labels and right-aligned notes.
    Bars show USED percentage (filled = used, empty = remaining).
    Returns y after both rows.
    """
    # Compute max note width so both bars are equal width
    nw1, _ = _sz(draw, row1_note, font)
    nw2, _ = _sz(draw, row2_note, font)
    note_x = W - PAD - max(nw1, nw2)

    bar_x = PAD + LBL_W
    bar_w = note_x - 4 - bar_x

    def _row(yr, label, pct, note):
        bar_y = yr + (ROW_H - BAR_H) // 2
        fh = font.size
        draw.text((PAD, bar_y + (BAR_H - fh) // 2), label, font=font, fill=BLACK)
        nw, nh = _sz(draw, note, font)
        draw.text((note_x, bar_y + (BAR_H - nh) // 2), note, font=font, fill=BLACK)
        if pct is not None:
            _bar_dots(draw, bar_x, bar_y, bar_w, BAR_H, pct)
        return yr + ROW_H

    y = _row(y, row1_label, row1_pct, row1_note)
    y = _row(y, row2_label, row2_pct, row2_note)
    return y


# ── Claude Code section ───────────────────────────────────────────────────────

def _draw_claude(draw: ImageDraw.ImageDraw, snap: dict, y: int) -> int:
    lbl = _f("pixel", 16)
    y = _section_label(draw, y, "CLAUDE CODE", lbl)

    if not snap.get("ok"):
        draw.text((PAD, y), snap.get("status", "error"), font=lbl, fill=BLACK)
        _, h = _sz(draw, "X", lbl)
        return y + h + 4

    pct_5h   = snap.get("pct_5h", 0)
    clear_5h = snap.get("clear_5h", "?")   # when window is FULLY empty
    pct_7d   = snap.get("pct_7d", 0)
    real_7d  = snap.get("real_7d", 0)

    rem_5h = 100 - pct_5h
    rem_7d = 100 - pct_7d

    # Row 1: 5h — bar = remaining capacity, note = "rem%  clr Xh"
    note_5h = f"{rem_5h}%  clr {clear_5h}" if clear_5h != "?" else f"{rem_5h}%"

    # Row 2: 7d — bar = remaining capacity, note = "rem%  real_tok"
    note_7d = f"{rem_7d}%  {format_tokens(real_7d)}"

    y = _dual_row(draw, y, lbl,
                  "5h", rem_5h, note_5h,
                  "7d", rem_7d, note_7d)
    return y


# ── Codex section ─────────────────────────────────────────────────────────────

def _draw_codex(draw: ImageDraw.ImageDraw, snap: dict, y: int) -> int:
    lbl = _f("pixel", 16)
    y = _section_label(draw, y, "CODEX", lbl)

    if not snap.get("ok"):
        draw.text((PAD, y), snap.get("raw_status", "error"), font=lbl, fill=BLACK)
        _, h = _sz(draw, "X", lbl)
        return y + h + 4

    s_used  = snap.get("short_used_percent")
    s_reset = snap.get("short_reset", "?")
    l_used  = snap.get("long_used_percent")
    l_reset = snap.get("long_reset", "?")

    def _note(used, reset):
        rem = 100 - used if used is not None else 0
        return f"{rem}%  {reset}" if reset and reset != "?" else f"{rem}%"

    y = _dual_row(draw, y, lbl,
                  snap.get("short_label", "5h"), s_used, _note(s_used, s_reset),
                  snap.get("long_label", "Wk"),  l_used, _note(l_used, l_reset))
    return y


# ── DeepSeek section ──────────────────────────────────────────────────────────

def _draw_deepseek(draw: ImageDraw.ImageDraw, snap: dict, y: int) -> int:
    lbl = _f("pixel", 16)
    big = _f("vcr",   20)
    y = _section_label(draw, y, "DEEPSEEK", lbl)

    if not snap.get("ok"):
        draw.text((PAD, y), snap.get("raw_status", "error"), font=lbl, fill=BLACK)
        _, h = _sz(draw, "X", lbl)
        return y + h + 4

    bal      = snap.get("balance")
    bal_text = f"{snap.get('symbol', '$')}{bal:.2f}" if bal is not None else "?"
    status   = snap.get("status", "ok").upper()

    draw.text((PAD, y), bal_text, font=big, fill=BLACK)
    _, bh = _sz(draw, bal_text, big)
    sw, sh = _sz(draw, status, lbl)
    draw.text((W - PAD - sw, y + bh - sh), status, font=lbl, fill=BLACK)
    return y + bh + 4


# ── Main render ───────────────────────────────────────────────────────────────

def render_image(snapshot: dict) -> bytes:
    img  = Image.new("L", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    tiny = _f("tiny", 8)
    ts   = snapshot.get("updated_at", datetime.now().strftime("%H:%M"))
    tsw, _ = _sz(draw, ts, tiny)
    draw.text((W - PAD - tsw, PAD), ts, font=tiny, fill=BLACK)

    y      = PAD
    claude = snapshot.get("claude", {})
    codex  = snapshot.get("codex", {})
    ds     = snapshot.get("deepseek", {})

    y = _draw_claude(draw, claude, y)

    if codex.get("ok"):
        y = _divider(draw, y + 3)
        _draw_codex(draw, codex, y)
    elif ds.get("ok"):
        y = _divider(draw, y + 3)
        _draw_deepseek(draw, ds, y)

    img = img.convert("1", dither=Image.Dither.NONE)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    from claude_usage import get_claude_usage
    snap = {
        "claude":   get_claude_usage(),
        "codex":    {"ok": False},
        "deepseek": {"ok": False},
        "updated_at": datetime.now().strftime("%H:%M"),
    }
    png = render_image(snap)
    out = Path(__file__).parent / "preview.png"
    out.write_bytes(png)
    print(f"Saved {out}")
    cl = snap["claude"]
    if cl["ok"]:
        print(f"  5h:  {cl['tok_5h']:>10,} tok  {cl['pct_5h']}%  reset {cl['reset_5h']}")
        print(f"  7d:  {cl['tok_7d']:>10,} tok  {cl['pct_7d']}%")
        print(f"  models: {cl['models']}")
