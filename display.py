#!/usr/bin/env python3
"""
Quote/0 AI usage display — Claude Code + optional Codex + optional DeepSeek.

Usage:
  python display.py               # render image + push to device
  python display.py --preview     # save preview.png, skip push
  python display.py --check       # self-check all configured services
  python display.py --debug-json  # print snapshot JSON, skip push
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

from claude_usage import get_claude_usage
from render import render_image

# ── Config ────────────────────────────────────────────────────────────────────

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

QUOTE0_API_KEY      = _env("QUOTE0_API_KEY")
QUOTE0_DEVICE_ID    = _env("QUOTE0_DEVICE_ID")
QUOTE0_IMAGE_TASK_KEY = _env("QUOTE0_IMAGE_TASK_KEY")
QUOTE0_TEXT_TASK_KEY  = _env("QUOTE0_TEXT_TASK_KEY")
QUOTE0_REFRESH_NOW  = _env("QUOTE0_REFRESH_NOW", "false").lower() == "true"
QUOTE0_PREVIEW_PATH = _env("QUOTE0_PREVIEW_PATH", str(Path(__file__).parent / "preview.png"))

DEEPSEEK_API_KEY    = _env("DEEPSEEK_API_KEY")

CODEX_AUTH_PATH     = Path.home() / ".codex" / "auth.json"
CODEX_USAGE_URL     = "https://chatgpt.com/backend-api/wham/usage"

API_BASE = "https://dot.mindreset.tech"

# ── Codex fetch (from quote0-burnout reference) ───────────────────────────────

def _load_codex_token():
    env_token = os.environ.get("CODEX_ACCESS_TOKEN", "").strip()
    if env_token:
        return env_token, os.environ.get("CODEX_ACCOUNT_ID", "").strip()
    if not CODEX_AUTH_PATH.exists():
        raise FileNotFoundError(f"No Codex credentials at {CODEX_AUTH_PATH}")
    with open(CODEX_AUTH_PATH) as f:
        auth = json.load(f)
    tokens = auth.get("tokens", {})
    return tokens.get("access_token", ""), tokens.get("account_id", "")


def get_codex_usage() -> dict:
    try:
        access_token, account_id = _load_codex_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "quote0-display",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        r = requests.get(CODEX_USAGE_URL, headers=headers, timeout=15)
        r.raise_for_status()
        return {"ok": True, "raw": r.json()}
    except FileNotFoundError as e:
        return {"ok": False, "status": "no auth", "detail": str(e)}
    except Exception as e:
        return {"ok": False, "status": "error", "detail": str(e)[:120]}


def _time_until(val) -> str:
    from datetime import timezone
    try:
        if isinstance(val, (int, float)):
            from datetime import datetime as dt
            d = dt.fromtimestamp(val, tz=timezone.utc)
        else:
            from datetime import datetime as dt
            d = dt.fromisoformat(str(val).replace("Z", "+00:00"))
    except Exception:
        return "?"
    secs = int((d - datetime.now(timezone.utc)).total_seconds())
    if secs <= 0:
        return "now"
    h, rem = divmod(secs, 3600)
    m = rem // 60
    if h >= 24:
        dd = h // 24
        return f"{dd}d{h % 24}h" if h % 24 else f"{dd}d"
    return f"{h}h{m:02d}m" if h and m else (f"{h}h" if h else f"{m}m")


def build_codex_snapshot(raw: dict) -> dict:
    if not raw.get("ok"):
        return {"ok": False, "raw_status": raw.get("status", "error")}
    rl   = raw.get("raw", {}).get("rate_limit", {})
    pri  = rl.get("primary_window", {})
    sec  = rl.get("secondary_window", {})

    def _pct(v):
        try:
            return int(float(v))
        except Exception:
            return None

    sp = _pct(pri.get("used_percent"))
    lp = _pct(sec.get("used_percent"))

    def _status(pct):
        if pct is None:
            return "unknown"
        return "hot" if pct >= 90 else ("warn" if pct >= 70 else "ok")

    return {
        "ok": True,
        "short_label": "5h",
        "short_used_percent": sp,
        "short_reset": _time_until(pri.get("reset_at")),
        "long_label": "Wk",
        "long_used_percent": lp,
        "long_reset": _time_until(sec.get("reset_at")),
        "status": _status(sp),
    }


# ── DeepSeek fetch ────────────────────────────────────────────────────────────

def get_deepseek_balance() -> dict:
    if not DEEPSEEK_API_KEY:
        return {"ok": False, "status": "no key"}
    try:
        r = requests.get(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Accept": "application/json"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        infos = data.get("balance_infos", [])
        usd = next((x for x in infos if x.get("currency") == "USD"), infos[0] if infos else None)
        if not usd:
            return {"ok": False, "status": "no balance"}
        SYMBOLS = {"USD": "$", "CNY": "¥", "EUR": "€", "GBP": "£"}
        currency = usd.get("currency", "USD")
        amount = float(usd.get("total_balance", 0))
        available = data.get("is_available")
        status = "hot" if not available or amount < 3 else ("warn" if amount < 10 else "ok")
        return {"ok": True, "balance": amount, "currency": currency,
                "symbol": SYMBOLS.get(currency, "$"), "status": status}
    except Exception:
        return {"ok": False, "status": "error"}


# ── Snapshot ──────────────────────────────────────────────────────────────────

def build_snapshot() -> dict:
    claude_data = get_claude_usage()

    codex_raw = get_codex_usage()
    codex     = build_codex_snapshot(codex_raw)

    deepseek  = get_deepseek_balance()

    return {
        "claude":     claude_data,
        "codex":      codex,
        "deepseek":   deepseek,
        "updated_at": datetime.now().strftime("%H:%M"),
    }


# ── Push ──────────────────────────────────────────────────────────────────────

def push_image(png_bytes: bytes) -> dict:
    url = f"{API_BASE}/api/authV2/open/device/{QUOTE0_DEVICE_ID}/image"
    payload = {
        "refreshNow": QUOTE0_REFRESH_NOW,
        "image": base64.b64encode(png_bytes).decode(),
        "ditherType": "DIFFUSION",
        "ditherKernel": "FLOYD_STEINBERG",
        "border": 0,
    }
    if QUOTE0_IMAGE_TASK_KEY:
        payload["taskKey"] = QUOTE0_IMAGE_TASK_KEY
    r = requests.post(
        url, json=payload,
        headers={"Authorization": f"Bearer {QUOTE0_API_KEY}"},
        timeout=20,
    )
    if not r.ok:
        try:
            body = r.json()
        except Exception:
            body = {"_raw": r.text}
        return {"ok": False, "status": r.status_code, "body": body}
    return {"ok": True, "body": r.json()}


# ── Check ─────────────────────────────────────────────────────────────────────

def check() -> int:
    print("quote display — self-check\n")
    failures = 0

    def row(label, ok, detail=""):
        tag = "OK  " if ok else "FAIL"
        print(f"  {label:<28} {tag}  {detail}")

    # Claude Code
    print("Claude Code:")
    cu = get_claude_usage()
    if cu["ok"]:
        row("sessions", True,
            f"5h {cu['pct_5h']}% ({cu['tok_5h']:,} tok)  7d {cu['tok_7d']:,} tok  reset {cu['reset_5h']}")
    else:
        row("stats-cache.json", False, cu.get("status", ""))
        failures += 1
    print()

    # Codex (optional)
    print("Codex (optional):")
    try:
        token, _ = _load_codex_token()
        if token:
            raw = get_codex_usage()
            sn  = build_codex_snapshot(raw)
            if sn["ok"]:
                row("usage", True, f"5h {sn['short_used_percent']}% [{sn['status']}]")
            else:
                row("usage", False, sn.get("raw_status", ""))
        else:
            row("auth", False, "empty token")
    except FileNotFoundError:
        row("auth", True, "not configured — skipped")
    print()

    # DeepSeek (optional)
    print("DeepSeek (optional):")
    if DEEPSEEK_API_KEY:
        ds = get_deepseek_balance()
        if ds["ok"]:
            row("balance", True, f"{ds['symbol']}{ds['balance']:.2f} [{ds['status']}]")
        else:
            row("balance", False, ds.get("status", ""))
    else:
        row("key", True, "not configured — skipped")
    print()

    # Render
    print("Render:")
    try:
        snap = build_snapshot()
        png  = render_image(snap)
        Path(QUOTE0_PREVIEW_PATH).write_bytes(png)
        row("image", True, QUOTE0_PREVIEW_PATH)
    except Exception as e:
        row("image", False, str(e))
        failures += 1
    print()

    # Quote/0 endpoint
    print("Quote/0:")
    if QUOTE0_API_KEY and QUOTE0_DEVICE_ID:
        try:
            r = requests.get(
                f"{API_BASE}/api/authV2/open/device/{QUOTE0_DEVICE_ID}/fixed/list",
                headers={"Authorization": f"Bearer {QUOTE0_API_KEY}"},
                timeout=10,
            )
            row("endpoint", r.ok, f"HTTP {r.status_code}")
            if not r.ok:
                failures += 1
        except Exception as e:
            row("endpoint", False, str(e))
            failures += 1
    else:
        row("endpoint", False, "QUOTE0_API_KEY or QUOTE0_DEVICE_ID missing")
        failures += 1
    print()

    if failures == 0:
        print("Result: OK")
    else:
        print(f"Result: FAIL ({failures} error(s))")
    return min(failures, 1)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Push AI usage to Quote/0 display")
    parser.add_argument("--preview",    action="store_true", help=f"Save preview PNG to {QUOTE0_PREVIEW_PATH}, skip push")
    parser.add_argument("--check",      action="store_true", help="Self-check, no push")
    parser.add_argument("--debug-json", action="store_true", help="Print snapshot JSON, no push")
    args = parser.parse_args()

    if args.check:
        sys.exit(check())

    snapshot = build_snapshot()

    if args.debug_json:
        print(json.dumps(snapshot, indent=2, default=str))
        sys.exit(0)

    png = render_image(snapshot)

    if args.preview:
        Path(QUOTE0_PREVIEW_PATH).write_bytes(png)
        print(f"Preview saved → {QUOTE0_PREVIEW_PATH}")
        cl = snapshot["claude"]
        if cl["ok"]:
            print(f"Claude: {cl['msgs_today']}msg today  5h {cl['pct_5h']}% used ({cl['real_5h']:,} real tok)  clr {cl['clear_5h']}")
        else:
            print(f"Claude: {cl.get('status', 'error')}")
        sys.exit(0)

    result = push_image(png)
    print(json.dumps({"ok": result["ok"], "body": result.get("body", {})}, indent=2))
    if not result["ok"]:
        print(f"\nPush failed (HTTP {result.get('status')})", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    # Auto-load .env if present
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        # Reload config after .env
        import importlib
        import display as _self
        for attr in ["QUOTE0_API_KEY", "QUOTE0_DEVICE_ID", "QUOTE0_IMAGE_TASK_KEY",
                     "QUOTE0_TEXT_TASK_KEY", "DEEPSEEK_API_KEY"]:
            val = os.environ.get(attr, "")
            setattr(_self, attr, val)
        QUOTE0_API_KEY    = os.environ.get("QUOTE0_API_KEY", "")
        QUOTE0_DEVICE_ID  = os.environ.get("QUOTE0_DEVICE_ID", "")
        DEEPSEEK_API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")

    main()
