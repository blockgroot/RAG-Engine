# Which Redis to use

> **Decision:** Render Key Value (Valkey 8), Starter plan, `volatile-lru`, in production.
> Nothing on staging. A Valkey container in the self-hosted install.

## What Handbook keeps in Redis

| Data | Size | Expires | If lost |
|---|---|---|---|
| Rate-limit buckets (`rl:*`) | bytes per person/company | yes, on its own | allowances reset to full |
| Answers being written at once (`rl:answers:*`) | one entry per answer | yes, 5-minute lease | the cap resets |
| Answer cache (`qa:<org>:<generation>:<hash>`) | up to 256 KB per answer | yes, TTL | cache miss, answer is rebuilt |
| Cache generation per company (`qa:gen:<org>`) | one number | **no** | see below |

Everything is disposable, so **no persistence or backups are needed**. What matters is a
fast, private connection from the API and a memory policy that never evicts the
generation counters.

## Memory policy: `volatile-lru`

When memory is full, Redis evicts keys by its `maxmemory-policy`.

- `allkeys-lru` may evict a company's generation counter. It then falls back to an older
  number, and answers cached **before the last sync** become visible again: a company
  could see an outdated answer after its documents changed.
- `volatile-lru` evicts only keys that have an expiry (cached answers, buckets). The
  counters stay. This is the setting to use everywhere.
- `noeviction` would refuse writes when full: the cache would stop storing and the limiter
  would fail open. Safe, but worse than `volatile-lru`.

## What production systems use

- **Stripe** runs its API rate limiters as token buckets on Redis.
- **GitHub** moved its API rate limiter from a shared Memcached to Redis. One reason:
  the shared cache, when full, evicted live rate-limit data. Same lesson as above.
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
network only (no allowed IPs), Maxmemory policy **`volatile-lru`**. The Free plan works but
holds 25 MB (100–200 cached answers) and is wiped on every restart, which resets every
allowance; Starter avoids both for $10.

**Staging and local.** No Redis. Limits count in each process's memory, exact for one
server. Never point staging at production's Redis: they would share limits.

**Self-hosted install.** `valkey/valkey:8-alpine` in `deploy/selfhost/docker-compose.yml`,
256 MB, `volatile-lru`, no persistence. Same commands and the same `redis` Python client,
so no code changes. Existing installs keep their own compose file and run without Redis
until they take the new one.

**Later, at scale.** Move up a plan when memory is regularly full (cache misses rise). With
several large API servers, give the rate limiter its own instance, apart from the cache
(GitHub's lesson): a cache filling up should never touch rate-limit data.

## Production checklist (Render dashboard, by hand)

1. Key Value instance → **Info**: plan **Starter** (moving off Free restarts it; only the
   limits and cache reset).
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
