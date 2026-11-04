# Plan: Rate limiting in Handbook

> **Status: PROPOSED, needs approval.** How we will stop any person, script or company from
> flooding Handbook, using a **token bucket** stored in **Redis**.

## Why we need it

- **Every company shares one AI account.** One heavy user or company can use up the quota,
and everyone else's answers fail.
- **Every question costs money:** a model call plus search calls, all paid per call.
- **Answers take several seconds**, so a flood ties up our servers.

## The strategy: token bucket

Each person has an allowance of **up to 5 questions** that can be used at once:

- Each question uses **1** from the allowance.
- The allowance is restored at a steady rate of **1 every 6 seconds** (10 a minute), up to the
maximum of 5.
- When the allowance is used up, the person sees "Too many requests, please try again in a
few seconds."

What that means in practice:

| Who                            | What happens                                                                                                       |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| **A normal person**            | Asks 3 quick questions, then reads for a minute while the allowance is restored. Never notices a limit.            |
| **A script or flood**          | Uses the 5 at once, then is held to 1 question every 6 seconds. 2,400 requests in 2 minutes became 24 in test ran. |
| **Someone who hits the limit** | Can ask again in about **6 seconds**, and is told exactly how long.                                                |

### Why this strategy

1. **Allows normal use, stops floods.** People ask a few questions quickly, then pause. The

   allowance permits that short burst and holds everyone to 10 a minute over time.
2. **Short, clear waits.** A refused person can ask again in seconds, not after waiting for a

   full minute to reset.
3. **No loophole.** The allowance is restored gradually, so there is no "start of the minute" moment to

   exploit by sending a second batch.
4. **Proven.** Stripe uses a token bucket on Redis for its whole API, and the Claude API (our

   own AI provider) uses one too.
5. **Cheap.** Two numbers per limit in Redis (allowance left, time last updated).

## Per company or per person? Both.

Each **person** has a limit, and each **company** has its own larger limit. A question
must be within **both** limits.

| Limit | Why we need it |
|---|---|
| **Per person** | Stops one person or a broken script from hogging. Keeps things fair inside a company. |
| **Per company** | Every company shares our one AI account. Without it, a 500-person company could use the whole account and break answers for every other company. |

**Why not only one of them:**

- **Per person only:** 500 people × 10 a minute = 5,000 questions a minute from one company.
That one company starves all the others.
- **Per company only:** one person's script uses up the whole company's budget, and their
colleagues are refused.

**The company limit grows with company size.** A single fixed number would be too tight for a
500-person company and too loose for a 5-person one. Large APIs work the same way: Claude and
OpenAI limit per organization, and GitHub raises an installation's limit as it grows.

**Login is limited per IP address**, because no one is signed in yet.

## Proposed limits

Starting numbers. They are settings, so we can tune them without a code change.

| What | Applies to | Maximum at once | Sustained rate |
|---|---|---|---|
| Questions: chat, Agent mode, charts, questions asked in Slack | per person | 5 | 10 a minute |
| Questions | per company | 3 × active users (minimum 30) | 3 a minute × active users (minimum 30) |
| Chat answers being written at once | per person | at most 2 at a time | — |
| Chat answers being written at once | everyone, across all servers | at most 20 at a time (others wait up to 30 s) | — |
| Login link and email change requests | per IP address (login), per person (email change) | 10 | 20 a minute |
| Every API call (settings, lists, pages) | per person, per IP when signed out | 40 | 120 a minute |

Questions asked **in Slack** count against the same person and company limits as the web app, so Slack
is not a way around the limit.

## How it works in Handbook

1. **Limits are stored in Redis**, not on our servers. Every server reads the same counts, so it

   does not matter which server handles a request, and we can add servers freely.
2. **Each limit is one small Redis key** (`rl:user:<id>`, `rl:org:<id>`, `rl:ip:<address>`)

   holding two numbers. It deletes itself when unused, so no cleanup job is needed.
3. **One short Redis script** restores, checks and updates the person **and** company limits in a

   single step. Two servers can never both use the last remaining request, and a person who
    is refused never uses up their company's allowance.
4. **One limiter in our code**, applied to every route group by default. A new endpoint is

   limited without anyone remembering to add it.
5. **Vendor webhooks are not limited** (Slack events, GitHub webhooks). They come from the

   vendor, not a person, and are already verified by the vendor's signature.
6. **Over the limit:** we answer HTTP 429 with a `Retry-After` time, and the web app and Slack

   show "Too many requests, please try again in N seconds."
7. **If Redis is down:** requests are let through and we get an alert, so the product keeps

   working. Stripe designs its limiters the same way.
8. **Edge rule (Vercel/Cloudflare)** stays as an outer layer: a per-IP limit that drops large

   anonymous floods before they reach our servers.
9. **Answers being written at once** are counted in Redis too, so all servers share them:

   - a person already writing 2 answers is told to wait for one to finish;
   - beyond 20 for everyone, people wait briefly instead of the AI provider refusing them.

   A server that stops mid-answer frees its places after 5 minutes.
10. **Every refusal is logged** with the limit that refused it (for example
    `question:company org=…`), never the question or an IP address. These logs are how
    we finalise the numbers.

## What we need to set up

- **Redis:** a small managed Redis in production; a Redis container in the self-hosted setup.
- **Code:** the limiter, the Redis script, applying it to every route group, and the
"try again in N seconds" message in web and Slack.
- **Tests:** a flood is cut to the limit; the person and company limits both apply; limits
hold across two servers; Redis down lets requests through.

Rough effort: **4–5 days**, including the Redis setup.

## Going live: Redis

Without `REDIS_URL`, each server counts on its own, so N servers allow N times every limit.
Requests are never refused because Redis is missing or down; it only makes the limits
looser.

### Staging

No Redis: production only. Staging counts limits per process, which is exact for its one
instance. Never point staging at production's Redis, or the two would share limits.

### Production (Hand-Book, set by hand in the Render dashboard)

`render.yaml` does not manage production, so these steps are manual:

1. Render → **New → Key Value**.
   - Name: `handbook-redis`.
   - Region: **the same region as Hand-Book**. The private address only works within one
     region.
   - Plan: the smallest paid plan. The free one works, but Render may restart it at any
     time, which resets everyone's allowances. Rate-limit entries are tiny.
   - Maxmemory policy: `allkeys-lru`.
   - Allowed IPs: **none** (private network only).
2. Copy the instance's **Internal Key Value URL** (`redis://red-…:6379`).
3. Hand-Book → **Environment** → add `REDIS_URL` with that URL → **Save**. This redeploys.
4. Check it:
   - the log no longer shows "REDIS_URL is not set";
   - asking 6 questions quickly gets the 6th refused with "Please try again in N
     seconds".

### Self-hosted

- **New installs** get a Redis container from `docker-compose.yml`, already connected.
- **Existing installs** keep their own compose file on upgrade, so they run without Redis.
  Limits are then counted inside the API server, which is exact for a single server. No
  action is needed.

## Decisions needed

1. Approve the **token bucket in Redis**, per person **and** per company.
2. Approve the starting limits above, especially:
   - person: 10 a minute, burst 5;
   - company: 3 a minute per active user, minimum 30.
3. OK to set up managed Redis in production?

## Sources

- Stripe, *Scaling your API with rate limiters* (token bucket on Redis; failing open):
https://stripe.com/blog/rate-limiters
- Claude API rate limits (token bucket; limits per organization):
https://platform.claude.com/docs/en/api/rate-limits
- OpenAI API rate limits (per organization and project):
https://developers.openai.com/api/docs/guides/rate-limits
- GitHub REST API rate limits (installation limit grows with size):
https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
