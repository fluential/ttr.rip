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

local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens')) or burst
local refill_rps = tonumber(redis.call('HGET', KEYS[1], 'refill_rps')) or start_rps
local max_tokens = tonumber(redis.call('HGET', KEYS[1], 'max_tokens')) or burst
local last_refill_ms = tonumber(redis.call('HGET', KEYS[1], 'last_refill_ms')) or now_ms
local backoff_until_ms = tonumber(redis.call('HGET', KEYS[1], 'backoff_until_ms')) or 0

if now_ms < backoff_until_ms then
  local retry_ms = backoff_until_ms - now_ms
  redis.call('PEXPIRE', KEYS[1], ttl_ms)
  return {0, retry_ms, tokens, refill_rps, max_tokens, backoff_until_ms}
end

local delta_ms = now_ms - last_refill_ms
if delta_ms < 0 then delta_ms = 0 end
tokens = math.min(max_tokens, tokens + delta_ms * (refill_rps / 1000.0))
last_refill_ms = now_ms

local allowed = 0
local retry_ms = 0

if tokens >= weight then
  tokens = tokens - weight
  allowed = 1
else
  if refill_rps > 0 then
    retry_ms = math.ceil((weight - tokens) / refill_rps * 1000.0)
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
        res = await r.eval(_RESERVE_LUA, 1, key, now_ms, weight, START_RPS, BURST_MAX, ttl_ms)
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
