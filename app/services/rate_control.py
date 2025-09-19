"""
Global adaptive rate controller (per channel, identity) with AIMD and backoff.
Coordinates across workers using Redis with an atomic reserve operation.

API:
- await reserve(channel, identity, weight=1.0) -> (ok: bool, retry_after_sec: float | None, snapshot: dict)
- await report(channel, identity, outcome: str, retry_after: float | None = None, http_status: int | None = None)

Outcomes: 'success' | 'rate_limited' | 'transient_error' | 'permanent_error'

Identity examples:
- telegram + sha256(bot_token)[:10]
- slack/discord/webhook + sha256(webhook_url)[:10]
"""
from __future__ import annotations

import os
import time
from typing import Optional, Tuple, Dict, Any

from app.core.redis_pool import get_redis_connection

# Defaults (overridable via env)
START_RPS = float(os.getenv("RC_START_RPS", "0.5"))          # ~30/min
RPS_MIN = float(os.getenv("RC_RPS_MIN", "0.1"))
RPS_MAX = float(os.getenv("RC_RPS_MAX", "2.0"))
BURST_MAX = float(os.getenv("RC_BURST_MAX", "5"))

AI_STEP = float(os.getenv("RC_AI_STEP", "0.05"))             # +0.05 rps per success
MD_FACTOR = float(os.getenv("RC_MD_FACTOR", "0.5"))          # 429 hard reduce
MD_SOFT = float(os.getenv("RC_MD_SOFT", "0.8"))              # 5xx/timeouts softer reduce

BACKOFF_BASE = float(os.getenv("RC_BACKOFF_BASE", "2"))      # seconds
BACKOFF_MAX = float(os.getenv("RC_BACKOFF_MAX", "300"))      # seconds
BACKOFF_LEVEL_MAX = int(os.getenv("RC_BACKOFF_LEVEL_MAX", "6"))

STATE_TTL_SECONDS = int(os.getenv("RC_STATE_TTL_SECONDS", "86400"))  # 24h
# Ensure we always have a small drip rate (>= 1/min), even under errors
DRIP_RPS = max(float(os.getenv("RC_DRIP_RPS", str(1.0 / 60.0))), RPS_MIN)
# Assumed external rate limit per identity (per minute) for display purposes
ASSUMED_LIMIT_PER_MIN = int(os.getenv("RC_ASSUMED_LIMIT_PER_MIN", "30"))

# Exceptions for Celery integration
class RateLimitedError(Exception):
    def __init__(self, retry_after: Optional[float] = None):
        super().__init__("rate limited")
        self.retry_after = retry_after

class TransientSendError(Exception):
    pass


def _state_key(channel: str, identity: str) -> str:
    return f"rc:{channel}:{identity}:state"


# Atomic reserve via Lua (refill, honor backoff, grant or compute wait)
_RESERVE_LUA = """
local now_ms = tonumber(ARGV[1])
local weight = tonumber(ARGV[2])
local start_rps = tonumber(ARGV[3])
local burst = tonumber(ARGV[4])
local ttl_ms = tonumber(ARGV[5])
local min_rps = tonumber(ARGV[6]) or 0

local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens')) or burst
local refill_rps = tonumber(redis.call('HGET', KEYS[1], 'refill_rps')) or start_rps
local max_tokens = tonumber(redis.call('HGET', KEYS[1], 'max_tokens')) or burst
local last_refill_ms = tonumber(redis.call('HGET', KEYS[1], 'last_refill_ms')) or now_ms
local backoff_until_ms = tonumber(redis.call('HGET', KEYS[1], 'backoff_until_ms')) or 0

-- Effective rps honors a minimum drip to avoid complete stall
local eff_rps = math.max(refill_rps, min_rps)

if now_ms < backoff_until_ms then
  local retry_ms = backoff_until_ms - now_ms
  redis.call('PEXPIRE', KEYS[1], ttl_ms)
  return {0, retry_ms, tokens, refill_rps, max_tokens, backoff_until_ms}
end

local delta_ms = now_ms - last_refill_ms
if delta_ms < 0 then delta_ms = 0 end
tokens = math.min(max_tokens, tokens + delta_ms * (eff_rps / 1000.0))
last_refill_ms = now_ms

local allowed = 0
local retry_ms = 0

if tokens >= weight then
  tokens = tokens - weight
  allowed = 1
else
  if eff_rps > 0 then
    retry_ms = math.ceil((weight - tokens) / eff_rps * 1000.0)
  else
    retry_ms = ttl_ms
  end
end

redis.call('HSET', KEYS[1],
  'tokens', tokens,
  'refill_rps', refill_rps,
  'max_tokens', max_tokens,
  'last_refill_ms', last_refill_ms,
  'backoff_until_ms', backoff_until_ms
)
redis.call('PEXPIRE', KEYS[1], ttl_ms)

return {allowed, retry_ms, tokens, refill_rps, max_tokens, backoff_until_ms}
"""

async def reserve(channel: str, identity: str, weight: float = 1.0) -> Tuple[bool, Optional[float], Dict[str, Any]]:
    r = get_redis_connection()
    if r is None:
        # Fail-open if Redis is unavailable
        return True, None, {}
    try:
        now_ms = int(time.time() * 1000)
        ttl_ms = STATE_TTL_SECONDS * 1000
        key = _state_key(channel, identity)
        res = await r.eval(_RESERVE_LUA, 1, key, now_ms, weight, START_RPS, BURST_MAX, ttl_ms, DRIP_RPS)
        # res = [allowed, retry_ms, tokens, refill_rps, max_tokens, backoff_until_ms]
        allowed = bool(int(res[0]))
        retry_ms = float(res[1]) if res[1] is not None else 0.0
        snapshot = {
            "tokens": float(res[2]),
            "refill_rps": float(res[3]),
            "max_tokens": float(res[4]),
            "backoff_until_ms": float(res[5]),
        }
        return allowed, (retry_ms / 1000.0 if not allowed else None), snapshot
    except Exception:
        # Fail-open on any Redis error
        return True, None, {}


async def report(
    channel: str,
    identity: str,
    outcome: str,
    *,
    retry_after: Optional[float] = None,
    http_status: Optional[int] = None,
) -> None:
    """
    Update controller state based on outcome.
    """
    r = get_redis_connection()
    if r is None:
        return

    key = _state_key(channel, identity)
    now_ms = int(time.time() * 1000)

    try:
        state = await r.hgetall(key) or {}
        refill_rps = float(state.get("refill_rps", START_RPS))
        max_tokens = float(state.get("max_tokens", BURST_MAX))
        backoff_until_ms = int(float(state.get("backoff_until_ms", "0")))
        backoff_level = int(float(state.get("backoff_level", "0")))

        if outcome == "success":
            refill_rps = min(refill_rps + AI_STEP, RPS_MAX)
            # Slowly expand burst allowance up to BURST_MAX
            if max_tokens < BURST_MAX:
                max_tokens = min(BURST_MAX, max_tokens + 0.25)
            # Decay backoff level
            backoff_level = max(backoff_level - 1, 0)

        elif outcome == "rate_limited":
            refill_rps = max(refill_rps * MD_FACTOR, RPS_MIN)
            backoff_level = min(backoff_level + 1, BACKOFF_LEVEL_MAX)
            if retry_after is not None and retry_after > 0:
                backoff_until_ms = max(backoff_until_ms, now_ms + int(retry_after * 1000))
            else:
                # Exponential backoff
                exp_sec = min(BACKOFF_MAX, BACKOFF_BASE * (2 ** max(backoff_level - 1, 0)))
                backoff_until_ms = max(backoff_until_ms, now_ms + int(exp_sec * 1000))

        elif outcome == "transient_error":
            refill_rps = max(refill_rps * MD_SOFT, RPS_MIN)
            # Small cooldown (2s) to avoid tight loops
            backoff_until_ms = max(backoff_until_ms, now_ms + 2000)

        elif outcome == "permanent_error":
            # Leave rates mostly unchanged
            pass

        # Ensure a non-zero drip so the system can recover
        refill_rps = max(refill_rps, DRIP_RPS)

        await r.hset(
            key,
            mapping={
                "refill_rps": refill_rps,
                "max_tokens": max_tokens,
                "backoff_until_ms": backoff_until_ms,
                "backoff_level": backoff_level,
            },
        )
        await r.expire(key, STATE_TTL_SECONDS)
    except Exception:
        # Ignore update errors
        return


async def snapshot(channel: str, identity: str) -> Dict[str, Any]:
    """
    Read-only snapshot of current rate control state for (channel, identity).
    Computes effective drip and whether it's limited now (backoff or tokens < 1).
    """
    r = get_redis_connection()
    if r is None:
        # Fail-open: unknown state
        return {
            "enabled": True,
            "limited": False,
            "limited_reason": None,
            "current_rps": START_RPS,
            "min_rps": DRIP_RPS,
            "current_rps_per_minute": START_RPS * 60.0,
            "min_rps_per_minute": DRIP_RPS * 60.0,
            "assumed_limit_per_minute": ASSUMED_LIMIT_PER_MIN,
            "tokens": BURST_MAX,
            "max_tokens": BURST_MAX,
            "backoff_seconds_remaining": 0.0,
        }

    key = _state_key(channel, identity)
    now_ms = int(time.time() * 1000)
    try:
        state = await r.hgetall(key) or {}
        tokens = float(state.get("tokens", BURST_MAX) or BURST_MAX)
        refill_rps = float(state.get("refill_rps", START_RPS) or START_RPS)
        max_tokens = float(state.get("max_tokens", BURST_MAX) or BURST_MAX)
        last_refill_ms = int(float(state.get("last_refill_ms", now_ms) or now_ms))
        backoff_until_ms = int(float(state.get("backoff_until_ms", "0") or 0))

        eff_rps = max(refill_rps, DRIP_RPS)
        # Refill prediction
        delta_ms = max(0, now_ms - last_refill_ms)
        refilled_tokens = min(max_tokens, tokens + (delta_ms * (eff_rps / 1000.0)))
        backoff_seconds = max(0.0, (backoff_until_ms - now_ms) / 1000.0)

        limited = False
        limited_reason = None
        retry_after = 0.0
        if backoff_seconds > 0:
            limited = True
            limited_reason = "backoff"
            retry_after = backoff_seconds
        elif refilled_tokens < 1.0:
            limited = True
            limited_reason = "rate_limited"
            retry_after = (1.0 - refilled_tokens) / eff_rps if eff_rps > 0 else None

        return {
            "enabled": True,
            "limited": limited,
            "limited_reason": limited_reason,
            "retry_after_seconds": retry_after,
            "current_rps": eff_rps,
            "min_rps": DRIP_RPS,
            "current_rps_per_minute": eff_rps * 60.0,
            "min_rps_per_minute": DRIP_RPS * 60.0,
            "assumed_limit_per_minute": ASSUMED_LIMIT_PER_MIN,
            "tokens": refilled_tokens,
            "max_tokens": max_tokens,
            "backoff_seconds_remaining": backoff_seconds,
        }
    except Exception:
        # On failure, provide minimal info rather than failing the UI
        return {
            "enabled": True,
            "limited": False,
            "limited_reason": None,
            "current_rps": START_RPS,
            "min_rps": DRIP_RPS,
            "current_rps_per_minute": START_RPS * 60.0,
            "min_rps_per_minute": DRIP_RPS * 60.0,
            "assumed_limit_per_minute": ASSUMED_LIMIT_PER_MIN,
            "tokens": BURST_MAX,
            "max_tokens": BURST_MAX,
            "backoff_seconds_remaining": 0.0,
        }
