"""
Alerting helpers: flapping detection and rate limiting.

Integration points (safely no-op without Redis):
- Call record_check_transition(check_id, new_status) whenever a check's status changes.
- Before sending any alert, call:
    ok, reason = await can_send_alert(check_id, channel="telegram", identity=bot_id)
    if not ok:
        # skip or queue message (reason is 'flapping_suppressed' or 'rate_limited')
        return

Defaults aim to keep Telegram under 30 messages/min per bot.
You can override via environment variables:
- FLAP_WINDOW_SECONDS (default 120)
- FLAP_THRESHOLD_CHANGES (default 6)  # transitions within window to consider flapping
- FLAP_SUPPRESSION_SECONDS (default 180)  # suppress alerts this long once flapping detected
- TELEGRAM_RATE_LIMIT_PER_MIN (default 30)
"""
from __future__ import annotations

import os
import time
from typing import Optional, Tuple

try:
    # Importing from your existing pool module (read-only)
    from app.core.redis_pool import get_redis_connection
except Exception:  # pragma: no cover
    get_redis_connection = None  # type: ignore[assignment]

# Defaults with environment overrides
FLAP_WINDOW_SECONDS = int(os.getenv("FLAP_WINDOW_SECONDS", "120"))
FLAP_THRESHOLD_CHANGES = int(os.getenv("FLAP_THRESHOLD_CHANGES", "6"))
FLAP_SUPPRESSION_SECONDS = int(os.getenv("FLAP_SUPPRESSION_SECONDS", "180"))

TELEGRAM_RATE_LIMIT_PER_MIN = int(os.getenv("TELEGRAM_RATE_LIMIT_PER_MIN", "30"))

# Redis key helpers
def _flap_history_key(check_id: int) -> str:
    return f"alerts:flap:history:{check_id}"

def _flap_suppress_key(check_id: int) -> str:
    return f"alerts:flap:suppress:{check_id}"

def _rate_key(channel: str, identity: str) -> str:
    return f"rate:{channel}:{identity}"


def _get_redis():
    if get_redis_connection is None:
        return None
    try:
        return get_redis_connection()
    except Exception:
        return None


def _now() -> float:
    return time.time()


async def record_check_transition(
    check_id: int,
    new_status: str,
    window_seconds: Optional[int] = None,
    threshold_changes: Optional[int] = None,
    suppression_seconds: Optional[int] = None,
    now: Optional[float] = None,
) -> bool:
    """
    Record a status transition for a check and enable temporary suppression if flapping.

    Returns True if flapping was detected and suppression engaged; False otherwise.
    """
    r = _get_redis()
    if r is None:
        return False  # No Redis: cannot persist, treat as not flapping

    window = window_seconds or FLAP_WINDOW_SECONDS
    threshold = threshold_changes or FLAP_THRESHOLD_CHANGES
    suppress_for = suppression_seconds or FLAP_SUPPRESSION_SECONDS
    ts = now or _now()

    hkey = _flap_history_key(check_id)
    entry = f"{int(ts)}:{new_status}"

    try:
        # Avoid counting non-transitions (same state as last)
        last = await r.lindex(hkey, 0)
        if isinstance(last, bytes):
            last = last.decode("utf-8")
        if last and last.split(":", 1)[-1] == new_status:
            # Still push for history but won't count as a flip
            await r.lpush(hkey, entry)
            await r.ltrim(hkey, 0, 199)  # keep recent ~200 events
            await r.expire(hkey, max(window * 3, suppress_for * 2))
            return False

        # Push new entry (most recent at index 0), keep bounded
        await r.lpush(hkey, entry)
        await r.ltrim(hkey, 0, 199)
        await r.expire(hkey, max(window * 3, suppress_for * 2))

        # Load recent history and count flips within window
        entries = await r.lrange(hkey, 0, -1)
        cutoff = ts - window

        flips = 0
        prev_status: Optional[str] = None
        for raw in entries:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                ets_str, status = raw.split(":", 1)
                ets = int(ets_str)
            except Exception:
                continue
            if ets < cutoff:
                break  # entries are in reverse-chronological order
            if prev_status is None:
                prev_status = status
                continue
            if status != prev_status:
                flips += 1
                prev_status = status

        if flips >= threshold:
            # Engage suppression
            skey = _flap_suppress_key(check_id)
            # set suppression marker with TTL
            await r.set(skey, "1", ex=suppress_for)
            return True

        return False
    except Exception:
        # On any Redis failure, fail-open: do not mark as flapping
        return False


async def should_suppress_alert(check_id: int) -> bool:
    """
    Check whether alerts are currently suppressed for a check due to flapping.
    """
    r = _get_redis()
    if r is None:
        return False
    try:
        val = await r.get(_flap_suppress_key(check_id))
        return bool(val)
    except Exception:
        return False


async def rate_limit_ok(
    channel: str,
    identity: Optional[str],
    limit_per_minute: Optional[int] = None,
    now_ms: Optional[int] = None,
) -> bool:
    """
    Sliding-window rate limiter per (channel, identity).
    - channel: e.g., 'telegram'
    - identity: e.g., bot_id or bot_token suffix to separate bots

    Returns True if under the limit and reservation was recorded; False if rate-limited.
    """
    if identity is None or identity == "":
        # Without identity, we can't limit per-bot; allow
        return True

    r = _get_redis()
    if r is None:
        return True

    limit = int(limit_per_minute or (TELEGRAM_RATE_LIMIT_PER_MIN if channel == "telegram" else 60))
    now_ms_val = int(now_ms or (time.time() * 1000))
    cutoff_ms = now_ms_val - 60_000
    key = _rate_key(channel, identity)

    try:
        # Clean old, check current count, then add current
        await r.zremrangebyscore(key, 0, cutoff_ms)
        current = await r.zcard(key)
        if current is None:
            current = 0
        if current >= limit:
            # Set a short TTL so we don't accumulate stale keys
            await r.expire(key, 120)
            return False
        # Use timestamp as both score and member for simplicity
        await r.zadd(key, {str(now_ms_val): now_ms_val})
        await r.expire(key, 120)
        return True
    except Exception:
        return True  # fail-open on Redis errors


async def can_send_alert(
    check_id: int,
    channel: str,
    identity: Optional[str] = None,
    *,
    respect_flapping: bool = True,
    limit_per_minute: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    Unified gate before sending an alert.

    Returns (ok, reason):
      - (True, 'ok') if allowed
      - (False, 'flapping_suppressed') if flapping suppression is active
      - (False, 'rate_limited') if channel identity has exceeded its per-minute limit
    """
    if respect_flapping and await should_suppress_alert(check_id):
        return False, "flapping_suppressed"

    if not await rate_limit_ok(channel, identity, limit_per_minute=limit_per_minute):
        return False, "rate_limited"

    return True, "ok"
