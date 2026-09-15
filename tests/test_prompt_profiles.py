"""Each source's escalation contact must belong to that source.

Every profile once carried the policy profile's "your HR team can help with
this", so an answer about an engineering Slack channel closed by directing the
asker to HR -- a team that did not write the content and cannot help with it.
A wrong contact is worse than no contact, because the asker acts on it.
"""

from __future__ import annotations

from app.rag import prompts


def _profiles():
    return {
        name: value
        for name, value in vars(prompts).items()
        if name.endswith("_PROMPT_PROFILE") and isinstance(value, prompts.PromptProfile)
    }


def test_only_the_policy_profile_mentions_hr():
    offenders = [
        name
        for name, profile in _profiles().items()
        if "hr" in profile.escalation_hint.lower() and name != "POLICY_PROMPT_PROFILE"
    ]
    assert offenders == [], f"non-policy profiles pointing at HR: {offenders}"


def test_every_profile_has_a_distinct_contact():
    """A shared hint is how the HR copy-paste spread in the first place."""
    hints = [p.escalation_hint for p in _profiles().values()]
    assert len(hints) == len(set(hints)), f"duplicated escalation hints: {hints}"


def test_the_hint_reaches_the_prompt():
    prompt = prompts.build_grounded_prompt(
        "what was decided?", ["a thread"], "no idea",
        profile=prompts.SLACK_PROMPT_PROFILE,
    )
    assert "the people in that channel can help with this" in prompt
    assert "HR" not in prompt
