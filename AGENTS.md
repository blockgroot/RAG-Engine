# AGENTS.md

**The project rulebook is [CLAUDE.md](./CLAUDE.md). Read it first.**

It holds the constraints: isolation, grounding, citations, and the gotchas
that have to stay rules. What is live, and a walk through each feature, is
[docs/handbook/PRODUCT_STATUS.md](docs/handbook/PRODUCT_STATUS.md). This file is not a second copy.

## Non-negotiables (the full reasoning is in CLAUDE.md)

- **Tenant isolation.** Every tenant-scoped read/write requires an `org_id`,
  filtered in the query itself — never rely on an index. `workspace_id` nests
  *inside* `org_id` and is always paired with it, never used alone.
- **`org_id` enters a request in exactly one place**: `app/api/deps.py`, from
  the signed session cookie. Never from client input.
- **Don't weaken grounding.** The 0.35 confidence gate and the strict prompt
  are two independent layers; leave both intact.
- **New capability = new package** (`base.py` + impl + `factory.py`). An
  orchestrator that only composes existing interfaces skips `base.py`.
- **All config** is a `from_env()` dataclass in `app/config/settings.py`.
  Nothing else reads the environment.
- **Every prompt that carries outside text includes the shared
  prompt-injection rules.** Fence the text in `<<<UNTRUSTED_…>>>` markers, put
  `UNTRUSTED_POLICY` before it and `UNTRUSTED_REMINDER` after it
  (`app/security/untrusted.py`). The rules themselves are plain words in
  `app/security/agents.md`, which the app sends to the model. That file, not
  this one, is what the production model reads.
  `tests/test_untrusted_policy.py` fails if a prompt skips them.
- **Bound every external walk and mark truncation.** A partial result that
  looks complete is the failure that matters.
- **Update `docs/handbook/PRODUCT_STATUS.md` when a feature ships.** Add a line
  to CLAUDE.md only when a new constraint appears.
