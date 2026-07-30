"""Heuristic scrubbing of instruction-like spans in untrusted text (Phase 16).

Prompt fencing alone is a partial mitigation — on this project's free LLM
endpoint a strong ``***SYSTEM*** … say six months`` payload still leaked into
answers ~60% of the time across a 15-run probe. Stripping the most common
injection *shapes* from retrieved/ingested text before they enter an LLM
prompt closes that specific hole without claiming a complete defence.

This is deliberately narrow (line/block heuristics, not an ML classifier) and
runs on *untrusted* surfaces only: retrieved chunks, contextualize inputs,
recovery snippets, web results. It must never touch the trusted instruction
prefix of our own prompts.
"""

from __future__ import annotations

import re

# Drop whole blocks that look like planted system/override directives.
_SYSTEM_BLOCK = re.compile(
    r"(?is)\*{0,3}\s*SYSTEM\s*\*{0,3}.*?(\*{0,3}\s*END\s*SYSTEM\s*\*{0,3}|$)"
)
_ASSISTANT_DIRECTIVE_BLOCK = re.compile(
    r"(?is)\[ASSISTANT DIRECTIVE[^\]]*\][\s\S]*?(?=\n#|\n<<<|$)"
)

# Drop individual lines that are almost certainly instructions, not policy facts.
_INJECTION_LINE = re.compile(
    r"(?i)^\s*("
    r"ignore\s+(all\s+)?(previous|earlier|prior)\b"
    r"|disregard\s+(any|all|every)\b"
    r"|you\s+are\s+no\s+longer\s+bound\b"
    r"|new\s+mandatory\s+instruction\b"
    r"|treat\s+this\s+as\s+a\s+higher-priority\b"
    r"|assistant\s+directive\b"
    r"|---\s*end\s+of\s+policy\s*---"
    r"|</\s*context\s*>"
    r"|</\s*policy\s*>"
    r").*"
)


def scrub_untrusted_text(text: str) -> str:
    """Remove common instruction-shaped spans from untrusted document/web text."""
    if not text:
        return text
    cleaned = _SYSTEM_BLOCK.sub("", text)
    cleaned = _ASSISTANT_DIRECTIVE_BLOCK.sub("", cleaned)
    kept = [
        line
        for line in cleaned.splitlines()
        if line.strip() and not _INJECTION_LINE.match(line)
    ]
    # Collapse leftover blank runs from removed blocks.
    out = "\n".join(kept)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out or text.strip()
