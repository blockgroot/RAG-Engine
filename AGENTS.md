# AGENTS.md

**The project rulebook is [CLAUDE.md](./CLAUDE.md). Read it first.**

It is the single source of truth for architecture, conventions, schema, and
gotchas. This file exists only so agents looking for `AGENTS.md` find their
way there — it is deliberately not a second copy. An earlier duplicate of the
rulebook lived here and had already drifted out of sync (it was missing an
entire feature's section), which is exactly the failure this avoids.

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
- **Bound every external walk and mark truncation.** A partial result that
  looks complete is the failure that matters.
- **Update CLAUDE.md at the end of each phase** — one dense line, not a
  narrative.
