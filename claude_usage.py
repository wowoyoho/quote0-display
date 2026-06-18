"""
Read Claude Code usage from ~/.claude/ JSONL session files.

Rate limits are based on REAL tokens (input + output only).
Cache tokens (cache_read / cache_creation) are excluded from pct calculations.

Key outputs:
  pct_5h    — real-token usage % in rolling 5h window
  clear_5h  — when the window is FULLY empty (latest msg + 5h): use this
              to know when you have full capacity again
  reset_5h  — when the FIRST capacity frees (oldest msg + 5h): first relief
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

STATS_CACHE  = Path.home() / ".claude" / "stats-cache.json"
PROJECTS_DIR = Path.home() / ".claude" / "projects"

WINDOW_5H = timedelta(hours=5)
WINDOW_7D = timedelta(days=7)


def _max_5h() -> int:
    """Real-token limit for 5h window. Tune until bar matches when you get throttled."""
    return int(os.environ.get("CLAUDE_5H_TOKEN_MAX", "500000"))

def _max_7d() -> int:
    return int(os.environ.get("CLAUDE_WEEK_TOKEN_MAX", "2000000"))


def _short_model(m: str) -> str:
    """claude-sonnet-4-6 → Sonnet 4.6"""
    m = m.replace("claude-", "")
    parts = m.split("-")
    family = parts[0].title()
    version = [p for p in parts[1:] if p[:1].isdigit() and len(p) <= 3]
    return f"{family} {'.'.join(version[:2])}" if version else family


def _time_until(dt: datetime | None) -> str:
    if dt is None:
        return "?"
    secs = int((dt - datetime.now(timezone.utc)).total_seconds())
    if secs <= 0:
        return "now"
    h, rem = divmod(secs, 3600)
    m = rem // 60
    if h >= 24:
        d = h // 24
        return f"{d}d{h%24}h" if h % 24 else f"{d}d"
    return f"{h}h{m:02d}m" if h and m else (f"{h}h" if h else f"{m}m")


def _parse_sessions() -> dict:
    """
    Scan JSONL session files. For each assistant message, record:
      - timestamp (from JSONL field, UTC)
      - real_tokens = input_tokens + output_tokens  (counts toward rate limit)
      - cache_tokens = cache_read + cache_creation   (informational only)

    Returns bucketed totals for 5h and 7d windows, plus window boundary timestamps.
    """
    now       = datetime.now(timezone.utc)
    cut_5h    = now - WINDOW_5H
    cut_7d    = now - WINDOW_7D
    today_str = date.today().isoformat()

    real_5h: int = 0
    real_7d: int = 0
    cache_5h: int = 0
    cache_7d: int = 0
    models_7d: dict[str, int] = {}

    msgs_today: int = 0
    tools_today: int = 0

    # Track oldest/newest message timestamps inside the 5h window
    earliest_5h: datetime | None = None
    latest_5h:   datetime | None = None

    for sf in PROJECTS_DIR.glob("**/*.jsonl"):
        # Quick pre-filter: skip files not modified in last 7 days
        try:
            mtime = datetime.fromtimestamp(sf.stat().st_mtime, tz=timezone.utc)
            if mtime < cut_7d:
                continue
        except OSError:
            continue

        with open(sf, errors="ignore") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts_str = obj.get("timestamp")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except Exception:
                    continue

                if ts < cut_7d:
                    continue

                msg   = obj.get("message", {}) or {}
                role  = msg.get("role") or obj.get("type", "")
                model = msg.get("model") or ""

                if role == "assistant" and not model.startswith("<"):
                    usage = msg.get("usage") or {}
                    real  = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                    cache = (usage.get("cache_read_input_tokens", 0)
                             + usage.get("cache_creation_input_tokens", 0))

                    if real + cache == 0:
                        continue

                    # 7d bucket
                    real_7d  += real
                    cache_7d += cache
                    if model:
                        models_7d[model] = models_7d.get(model, 0) + real

                    # 5h window
                    if ts >= cut_5h:
                        real_5h  += real
                        cache_5h += cache
                        if earliest_5h is None or ts < earliest_5h:
                            earliest_5h = ts
                        if latest_5h is None or ts > latest_5h:
                            latest_5h = ts

                    # Today (local date)
                    if ts.astimezone().date().isoformat() == today_str:
                        msgs_today += 1

                elif role == "user" and ts.astimezone().date().isoformat() == today_str:
                    content = msg.get("content") or []
                    if isinstance(content, list):
                        tools_today += sum(
                            1 for c in content
                            if isinstance(c, dict) and c.get("type") == "tool_result"
                        )

    # Window boundary times
    # reset_5h = first capacity freed (oldest msg ages out)
    # clear_5h = full window empty (newest msg ages out) ← most useful to display
    reset_5h = (earliest_5h + WINDOW_5H) if earliest_5h else None
    clear_5h = (latest_5h   + WINDOW_5H) if latest_5h   else None

    return {
        "real_5h":    real_5h,
        "cache_5h":   cache_5h,
        "real_7d":    real_7d,
        "cache_7d":   cache_7d,
        "models_7d":  models_7d,
        "msgs_today": msgs_today,
        "tools_today": tools_today,
        "reset_5h":   reset_5h,   # first relief
        "clear_5h":   clear_5h,   # fully clear
    }


def _pct(used: int, max_val: int) -> int:
    if max_val <= 0:
        return 0
    return min(100, int(used / max_val * 100))


def _status(p: int) -> str:
    return "hot" if p >= 90 else ("warn" if p >= 70 else "ok")


def get_claude_usage() -> dict:
    """Full Claude Code usage snapshot. Never raises."""
    try:
        parsed = _parse_sessions()

        max5 = _max_5h()
        max7 = _max_7d()

        real_5h = parsed["real_5h"]
        real_7d = parsed["real_7d"]

        p5 = _pct(real_5h, max5)
        p7 = _pct(real_7d, max7)

        models = {
            _short_model(k): v
            for k, v in parsed["models_7d"].items()
            if not k.startswith("<")
        }

        # Session count from stats-cache (JSONL doesn't track this easily)
        today_sessions = 0
        if STATS_CACHE.exists():
            try:
                with open(STATS_CACHE) as f:
                    cache = json.load(f)
                today_str = date.today().isoformat()
                entry = next(
                    (d for d in reversed(cache.get("dailyActivity", [])) if d["date"] == today_str),
                    None,
                )
                today_sessions = (entry or {}).get("sessionCount", 0)
            except Exception:
                pass

        return {
            "ok": True,
            # 5h rolling window (rate limit relevant)
            "real_5h":    real_5h,
            "cache_5h":   parsed["cache_5h"],
            "pct_5h":     p5,
            "status_5h":  _status(p5),
            "max_5h":     max5,
            "reset_5h":   _time_until(parsed["reset_5h"]),   # first capacity freed
            "clear_5h":   _time_until(parsed["clear_5h"]),   # fully clear again
            # 7d window
            "real_7d":    real_7d,
            "cache_7d":   parsed["cache_7d"],
            "pct_7d":     p7,
            "status_7d":  _status(p7),
            "max_7d":     max7,
            # Today activity
            "msgs_today":     parsed["msgs_today"],
            "tools_today":    parsed["tools_today"],
            "today_sessions": today_sessions,
            # Model breakdown (7d, real tokens)
            "models": models,
        }

    except Exception as e:
        return {"ok": False, "status": str(e)[:200]}


def format_tokens(n: int) -> str:
    """1_234_567 → '1.2M',  45_000 → '45K'"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}K"
    return str(n)
