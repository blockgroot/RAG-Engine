# Which Redis to use

> **Decision:** Render Key Value (Valkey 8) in production: Free while usage is small,
> Starter ($10/mo) once customers depend on it. Nothing on staging. A Valkey container in
> the self-hosted install. Redis holds **rate limits only** (the answer cache was removed
> on 2026-11-06).

## What Handbook keeps in Redis

| Data | Size | Expires | If lost |
|---|---|---|---|
| Rate-limit buckets (`rl:*`) | bytes per person/company | yes, on its own | allowances reset to full |
| Answers being written at once (`rl:answers:*`) | one entry per answer | yes, 5-minute lease | the cap resets |

Measured: about 110 bytes per bucket, and a person uses 3–4 at a time, each expiring
20–60 s after their last request. 1,000 people active at once is about 0.4 MB; an empty
server is about 0.9 MB. **25 MB is far more than enough.**

Everything is disposable, so **no persistence or backups are needed**. What matters is a
fast, private connection from the API.

## Memory policy: `volatile-lru`

When memory is full, Redis evicts keys by its `maxmemory-policy`. Every key Handbook
writes has an expiry, so `volatile-lru` and `allkeys-lru` behave the same today. Use
`volatile-lru`: if a key without an expiry is ever added, it is never evicted by surprise.
`noeviction` would refuse writes when full and the limiter would fail open.

## What production systems use

- **Stripe** runs its API rate limiters as token buckets on Redis.
- **GitHub** moved its API rate limiter from a shared Memcached to Redis. One reason:
  the shared cache, when full, evicted live rate-limit data. Ours holds nothing else.
- **Valkey** is the Linux Foundation fork of Redis (BSD licence), created when Redis left
  open source in 2024. AWS ElastiCache, Google Memorystore, DigitalOcean, Aiven and
  **Render** now offer it; new Render Key Value instances run Valkey 8. GitLab supports
  Valkey as a backend.
- **Redis licence:** Redis 7.4 is RSALv2/SSPL (not open source). Redis 8 added AGPLv3,
  which many legal teams review before shipping. Valkey has neither concern.

## Options compared

| Option | Cost | For us |
|---|---|---|
| **Render Key Value** | Free: 25 MB, no persistence, one per workspace. **Starter $10/mo, 256 MB**. Standard $32/mo, 1 GB | Same private network as the API, never on the internet, no extra vendor. **Chosen.** |
| Upstash | Free: 500k commands/month. $0.20 per 100k, or fixed from $10/mo | Over the public internet (slower per call). Every API call is a command, so the free tier runs out fast. Good for edge functions, which we don't use. |
| Redis Cloud / ElastiCache | from about $15–20/mo | Separate account and network. Overkill at our size. |
| Self-run Valkey container | free | Right for the self-hosted install. |

## By stage

**Production (now).** Render Key Value, **Starter**, same region as Hand-Book, private
network only (no allowed IPs), Maxmemory policy **`volatile-lru`**. Free (25 MB) holds
everything we store; but Render may restart it at any time, which resets every allowance,
and its connection limit is lower. Move to Starter ($10) once customers depend on it.

**Staging and local.** No Redis. Limits count in each process's memory, exact for one
server. Never point staging at production's Redis: they would share limits.

**Self-hosted install.** `valkey/valkey:8-alpine` in `deploy/selfhost/docker-compose.yml`,
256 MB, `volatile-lru`, no persistence. Same commands and the same `redis` Python client,
so no code changes. Existing installs keep their own compose file and run without Redis
until they take the new one.

**Later, at scale.** Move up a plan for more connections when several API servers run.
If anything else is ever stored in Redis (a cache, a queue), give it its own instance
(GitHub's lesson): something else filling up should never touch rate-limit data.

## Production checklist (Render dashboard, by hand)

1. Key Value instance → **Info**: plan Free now, **Starter** once customers depend on it
   (moving off Free restarts it; only the limits reset).
2. **Maxmemory policy: `volatile-lru`.**
3. Region is Hand-Book's; **Allowed IPs** empty.
4. Hand-Book's log at startup still says "Rate limits: Redis at …, connected".

## Sources

- Render Key Value: https://render.com/docs/key-value
- Render moves Key Value to Valkey 8: https://render.com/changelog/new-render-key-value-instances-run-valkey-8
- Valkey on Render FAQ: https://render.com/docs/valkey-faq
- Render pricing (third-party summary): https://www.budgetforge.dev/tools/render-pricing-2026
- Upstash Redis pricing: https://upstash.com/pricing/redis.md
- GitHub's Redis rate limiter: https://github.blog/engineering/infrastructure/how-we-scaled-github-api-sharded-replicated-rate-limiter-redis/
- Stripe, rate limiters: https://stripe.com/blog/rate-limiters
- Valkey turns two (AWS): https://aws.amazon.com/blogs/database/valkey-turns-two/
- GitLab Redis and Valkey: https://docs.gitlab.com/administration/redis/
- Redis licensing explained: https://oneuptime.com/blog/post/2026-03-31-redis-licensing-sspl-agplv3-explained/view
