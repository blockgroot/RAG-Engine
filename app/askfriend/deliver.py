"""Getting the note to the colleague: a DM from the Handbook bot, or an email
whose Reply-To is the asker. Either way, replying to the asker is one tap; we
do not track the reply.
"""

from __future__ import annotations

import logging

from ..auth.credentials import get_live_connection_token
from ..auth.email import EmailOutcomeUnknown, _dispatch
from ..core.exceptions import ProviderError
from ..sources.slack_utils import has_tagged_message, lookup_user_id, open_dm, post_message_outcome

logger = logging.getLogger(__name__)


class DeliveryError(ProviderError):
    """The note did not reach the colleague; nothing was recorded, so the asker can retry."""


class DeliveryUnknown(ProviderError):
    """It went out and no answer came back: it may have arrived. Never retried
    automatically (a retry could send it twice); the asker is told and decides."""


#: Tags our DM so a post whose answer never came back can be looked up.
_SLACK_EVENT = "handbook_ask_friend"


def slack_token(org_id: str, workspace_id: str | None) -> str | None:
    """The space's Slack bot, else the company's, else None. The DM carries only
    the asker's own question and summary, so the company bot may deliver it."""
    scopes = [workspace_id, None] if workspace_id else [None]
    for scope in scopes:
        try:
            return get_live_connection_token(org_id, "slack", workspace_id=scope)
        except ProviderError:
            continue
    return None


def compose(asker_name: str, summary: str, asker_mention: str | None, *, via: str) -> str:
    """The message text. ``asker_mention`` is ``<@U…>`` when the asker is in Slack."""
    who = f"{asker_name} ({asker_mention})" if asker_mention else asker_name
    if via == "slack":
        reply = f"Reply to {asker_mention} directly." if asker_mention else f"Reply to {asker_name} directly."
    else:
        reply = f"Just reply to this email to answer {asker_name}."
    return (
        f"{who} asked Handbook something it couldn't answer and thought you might know.\n\n"
        f"{summary}\n\n{reply}"
    )


def send_slack(token: str, recipient_email: str, asker_email: str, asker_name: str, summary: str,
               *, key: str) -> None:
    """DM the colleague. ``DeliveryError``: it did not arrive (retry is safe);
    ``DeliveryUnknown``: it may have. ``key`` names this ask: the DM carries it, so
    a timed-out post is looked up in the DM before anyone decides it failed."""
    recipient = lookup_user_id(token, recipient_email)
    if not recipient:
        raise DeliveryError("The colleague is not in this Slack")
    channel = open_dm(token, recipient)
    if not channel:
        raise DeliveryError("Could not open a Slack DM")
    asker = lookup_user_id(token, asker_email)
    text = compose(asker_name, summary, f"<@{asker}>" if asker else None, via="slack")
    outcome, _ = post_message_outcome(
        token, channel, text, metadata={"event_type": _SLACK_EVENT, "event_payload": {"key": key}}
    )
    if outcome == "sent":
        return
    if outcome == "rejected":
        raise DeliveryError("Slack did not accept the message")
    landed = has_tagged_message(token, channel, _SLACK_EVENT, key)
    if landed:
        return
    if landed is False:
        raise DeliveryError("Slack did not keep the message")
    raise DeliveryUnknown("Slack did not answer; the message may have arrived")


def send_email(recipient_email: str, asker_email: str, asker_name: str, summary: str) -> None:
    """Email the colleague with Reply-To = the asker. Raises ``DeliveryError``."""
    try:
        _dispatch(
            recipient_email,
            f"{asker_name} asked you a question via Handbook",
            compose(asker_name, summary, None, via="email"),
            None,
            reply_to=asker_email,
        )
    except EmailOutcomeUnknown as exc:
        raise DeliveryUnknown("The email may have been sent", cause=exc) from exc
    except ProviderError as exc:
        raise DeliveryError("The email was not sent", cause=exc) from exc
