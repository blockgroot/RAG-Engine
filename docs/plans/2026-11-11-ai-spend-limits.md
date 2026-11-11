# AI spend limits

> **Status: built on `feat/ai-spend-limits`.** Limits how much AI each person and company
> can use, measured in dollars, on top of the existing request limits.

## Why

Request limits count questions, but questions do not cost the same:

- On Claude, a typical Handbook answer is about **6k tokens**, a heavy one about **15k**, and
  the largest measured about **33k** (bench3 Haiku and bench4 Sonnet, 300+ answers).
- The two answering models (Claude Sonnet 5.5 and Haiku 5.5, the only picks) differ **20x** in price.

So "10 questions a minute" can mean very different spend. The constraint is a **$100 Claude
credit** for Syvora's **30 people**. Background work (syncing, tables, graph) runs on Gemini
and is not paid from it.

## Prices (dollars per million tokens)

| Model | Input / output | Typical question |
|---|---|---|
| Sonnet 5.5 (default) | $2 / $10 | ~$0.016 |
| Haiku 5.5 | $0.10 / $0.50 | ~$0.0008 (measured end to end: $0.000713) |

## Limits

| Limit | Value | On Sonnet | On Haiku 5.5 |
|---|---|---|---|
| Company, per day | **$3.00** (~$90/month; $10 margin) | ~190 questions | ~3,700 |
| Person, per day | **$0.30** | ~19 questions | ~375 |
| Person, per minute | burst **$0.15**, refill **$0.08/min** | 2 of the largest back to back | |

Production's busiest real person-day so far: 8 questions (about $0.13 even on Sonnet), so no
one is blocked today. All values are settings (`AI_*` in `.env.example`); 0 turns one off.

## How it works

1. **Charged where every call is made.** The model provider charges each finished call
   its real cost (input tokens x input price + output tokens x output price) to the person
   and company asking. Calls on an unpriced model (Gemini) cost nothing.
2. **Checked before a question.** With the request limits: if the person's or company's
   spend bucket is empty, the question is refused: "You've reached the AI usage limit for
   now. Please try again in N minutes/hours."
3. **One request may overdraw.** A request's cost is known only after it runs, so a bucket
   can go below zero by one request (at most ~$0.07); the next waits until it refills.
4. **Day buckets refill evenly** over the day, not all at midnight.
5. **Same Redis token buckets** as the request limits, holding dollars; shared by every
   server. If Redis is down, requests are let through and the error is logged.

## Also set by hand

- **Claude Console → Limits: a $100 monthly spend limit**, the hard stop at Anthropic.
- Benchmarks: set the `AI_*` limits to 0 in `.env.bench`, or a run is stopped part way.
