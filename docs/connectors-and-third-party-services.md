# Connectors and Third-Party Services

Prices as last known (late 2026). Confirm on vendor pricing pages before deciding.

## 1. Connector APIs

| Connector | APIs used | Free? | Paid requirement |
| --- | --- | --- | --- |
| Notion | Notion API v1 (search, pages, blocks, databases), webhooks, Notion MCP | Yes | None |
| Google | Drive v3, Docs v1, Sheets v4, Forms v1, Admin Directory, OAuth | Yes | Yearly security audit (CASA) for `drive.readonly`, to allow sign-in beyond 100 test users |
| Slack | Web API (conversations, users, chat, files), Events API, OAuth v2 | Yes | Marketplace approval, to avoid the rate limits Slack puts on distributed apps |
| Linear | GraphQL API, webhooks, OAuth, Linear MCP | Yes | Private teams need the customer's Linear Business plan |
| GitHub | REST API through a GitHub App, GitHub MCP | Yes | None (check whether GitHub MCP needs Copilot) |

## 2. Paid services the app needs

| Service | Used for | Free plan | Paid cost |
| --- | --- | --- | --- |
| Anthropic (Claude) | Answer model | None | Per token, about $10–50/month at small scale |
| Jina | Embeddings and reranking | One-time ~10M tokens | ~$0.05 per 1M tokens |
| Groq | Injection guard and moderation | Yes, rate-limited | By usage |
| SendGrid | Email (sign-in, reports) | Being replaced by a trial | From ~$20/month |
| Cloudinary | Chat file uploads | 25 credits/month | From ~$89/month |
| Tavily (optional) | Web search (DuckDuckGo is used now, free) | 1,000 credits/month | $30/month |
| Render | Backend API hosting | Yes, 512 MB, sleeps when idle | $25/month (Standard) |
| Vercel | Web app hosting | Hobby, non-commercial only | $20/month per seat (Pro) |
| Supabase | Database (Postgres + pgvector) | 500 MB, pauses when idle | $25/month (Pro) |
| Google CASA audit | Only if we keep `drive.readonly` in our shared Google app | — | A few hundred to a few thousand USD/year |

**Estimated minimum for paying customers:** about $80–180/month, plus the Google audit if needed.
