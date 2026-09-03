"""Charts over connected-source data.

An orchestrator, not a provider: it composes the existing db pool and LLM
interface rather than abstracting over a second backend, so per CLAUDE.md §2
there is deliberately no ``base.py`` here. Do not add one speculatively.
"""
