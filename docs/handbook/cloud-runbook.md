# Handbook Cloud runbook (internal)

*For the Handbook team only. Not shared with customers.* Every setting named here is read, with its default, in `app/config/settings.py`. Values are never written here.

The customer-facing rollout checklist (for example [syvora-rollout-checklist.md](syvora-rollout-checklist.md)) refers to this runbook with one line: "The Handbook team confirms the platform is ready."

---

## 1. Where things run

| Part | Service | Detail |
| --- | --- | --- |
| Web app | Vercel, `https://hand-book.vercel.app` | Requests to `/api` are forwarded to the API. `NEXT_PUBLIC_API_BASE_URL` is never pointed at Render. |
| API and worker | Render service **Hand-Book**, Singapore | Settings live in the Render dashboard only; `render.yaml` does not configure it. |
| Database | Supabase, Mumbai (`ap-south-1`) | Session pooler (`*.pooler.supabase.com`, port 5432), never the transaction pooler. |
| Rate limits and AI budgets | Render Key Value (Valkey 8) | `REDIS_URL`. Holds counters only, every key with a TTL. |
| Scheduler | GitHub Actions `tick` workflow, every 10 minutes | Starts the hourly re-sync and clean-up jobs. |

## 2. Platform readiness (before a new company starts)

- [ ] Render service **Hand-Book** is on the **Starter** plan (Render → Hand-Book → Settings → Instance type).
- [ ] Render Key Value is on **Starter**, and `REDIS_URL` is set on Hand-Book.
- [ ] Supabase is on **Pro** with daily backups on, and `DATABASE_URL` uses the session pooler.
- [ ] `FRONTEND_URL` is exactly `https://hand-book.vercel.app`. Every sign-in return address is built from it.
- [ ] `https://hand-book.onrender.com/health` returns 200, and `https://hand-book.vercel.app` opens with a valid certificate.
- [ ] The latest `tick` run succeeded within the last 20 minutes.
- [ ] `OWNER_NOTIFICATION_EMAIL` is the Handbook team's address, so organization requests reach us.
- [ ] `MASK_IDENTIFIERS` is not `false`, and the injection guard is on (`GUARD_MODE` is not `off`, the default). The guard runs on its own account when `GUARD_API_KEY` and `GUARD_BASE_URL` are both set (`GUARD_SAFEGUARD_MODEL` names the model there); with neither, it uses `GROQ_API_KEY`.
- [ ] Background work runs on the Gemini model, on its own quota: `LLM_AUX_API_KEY` with `LLM_AUX_BASE_URL` or `LLM_AUX_ADAPTER`, and `LLM_AUX_MODEL`. Base URL and key are both set or neither.
- [ ] `CLAUDE_API_KEY` is set (or `LLM_ADAPTER=anthropic` with `LLM_API_KEY`), so the picker offers Claude Sonnet 5.5 and Claude Haiku 5.5.
- [ ] `RATE_LIMIT_ENABLED` is not `false`.
- [ ] `LLM_MAX_RPM` matches the Anthropic account's requests-per-minute tier (the default 15 is a free-tier value).
- [ ] `AI_COMPANY_DOLLARS_PER_DAY` and `AI_PERSON_DOLLARS_PER_DAY` are set to the budget agreed with the company (defaults $3.00 and $0.30 are trial values).
- [ ] `AI_TOKEN_LIMITS` fits within the Anthropic account's tokens-per-minute limit for each model.
- [ ] `CHAT_MAX_CONCURRENT` (default 20) is no higher than the Anthropic and Jina plans serve at once.
- [ ] `AUTH_ENCRYPTION_KEYS` is in the team's password manager. Without it, every saved tool connection is unreadable after a database restore.

**Done when:** health returns 200, the tick is green, the plans are upgraded and the company's limits are set. Then tell the company's admin the platform is ready.

## 3. Email delivery

- [ ] `EMAIL_SENDER=sendgrid`, `EMAIL_SENDGRID_API_KEY` set, and `EMAIL_SMTP_FROM` set to the Cloud sender address, whose domain is verified in SendGrid.
- [ ] The SendGrid API key is kept only in Render and the team's password manager, never in a document, ticket or chat.

## 4. Running the platform

| How often | Task |
| --- | --- |
| Weekly | Check the `tick` workflow is green and Render shows no repeated errors |
| Monthly | Check Supabase backups exist and the database size fits the plan |
| Monthly | Check Anthropic and Jina usage against their plans, and Render logs for repeated "Too many requests" refusals |
| Every 60 days | Make at least one commit; GitHub turns off scheduled workflows after 60 days without activity |
| Each release | Write `release-notes/<version>.md`, deploy Cloud, then push the tag `v<version>` (`.github/workflows/release.yml`). Cloud admins see "What's new" beside the chat modes for 7 days, and open tabs show a "new version is ready" pill. Cloud sends no release email and has no Updates page. |

## 5. If something goes wrong

| What happened | First action | How fast |
| --- | --- | --- |
| Handbook is down | Check `/health`, Render and Supabase status; send an incident notice from the support email, and another when resolved | Within 4 working hours |
| People are often told "Too many requests" | Find the refusing limit in the Render logs; raise it on Render, or upgrade the Anthropic or Jina plan | Within 1 working day |
| Sign-in links not arriving | Check the SendGrid activity for that address | Same day |
| The SendGrid key was exposed | Create a new key in SendGrid, update it on Render, delete the old key | Same day |
