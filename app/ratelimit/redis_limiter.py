"""Token buckets in Redis, shared by every server instance.

One Lua script refills, checks and takes from every bucket in a single atomic
step, so two servers can never both take the last request. The clock is
Redis's own (``TIME``), so servers whose clocks differ still agree.

Each bucket is a hash ``{tokens, ts}`` that expires once it would be full
again: an idle bucket costs nothing and needs no cleanup job.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..core.exceptions import RateLimiterError
from .base import Bucket, Decision, RateLimiter

_KEY_PREFIX = "rl:"

# KEYS: bucket keys. ARGV: burst, per_minute for each key, in order.
# Returns {allowed (1/0), retry_after seconds as a string} (a Lua number in a
# reply is cut to an integer).
_TAKE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local tokens, wait = {}, 0
for i = 1, #KEYS do
  local burst = tonumber(ARGV[2 * i - 1])
  local rate = tonumber(ARGV[2 * i]) / 60
  local v = redis.call('HMGET', KEYS[i], 'tokens', 'ts')
  local tk = tonumber(v[1]) or burst
  local ts = tonumber(v[2]) or now
  tk = math.min(burst, tk + math.max(0, now - ts) * rate)
  tokens[i] = tk
  if tk < 1 then wait = math.max(wait, (1 - tk) / rate) end
end
local spend = 0
if wait == 0 then spend = 1 end
for i = 1, #KEYS do
  local burst = tonumber(ARGV[2 * i - 1])
  local rate = tonumber(ARGV[2 * i]) / 60
  redis.call('HSET', KEYS[i], 'tokens', tostring(tokens[i] - spend), 'ts', tostring(now))
  redis.call('EXPIRE', KEYS[i], math.ceil(burst / rate) + 1)
end
return {spend, tostring(wait)}
"""


class RedisRateLimiter(RateLimiter):
    def __init__(self, url: str) -> None:
        import redis

        # Short timeouts: the limiter sits in front of every request, and a
        # slow Redis must fail (and be let through) rather than stall them.
        self._client = redis.Redis.from_url(
            url, socket_connect_timeout=0.5, socket_timeout=0.5
        )
        self._take = self._client.register_script(_TAKE)

    def take(self, buckets: Sequence[Bucket]) -> Decision:
        if not buckets:
            return Decision(allowed=True)
        # ponytail: several keys in one script need one Redis node; a Redis
        # Cluster would need a shared {hash tag} per request.
        keys = [_KEY_PREFIX + b.key for b in buckets]
        args: list[float] = []
        for b in buckets:
            args += [b.burst, b.per_minute]
        try:
            allowed, wait = self._take(keys=keys, args=args)
        except Exception as exc:  # noqa: BLE001 - any client/server fault
            raise RateLimiterError("Rate limiter store unavailable", cause=exc) from exc
        return Decision(allowed=bool(int(allowed)), retry_after=float(wait))
