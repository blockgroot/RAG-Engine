"""Google Forms responses: read live, NEVER indexed.

Not a ``SourceAdapter``: an anonymous survey must not become answerable
("what did Ada say?"). Responses are classified once into a sentiment label
(``app/insights/sentiment.py``) and the text is discarded.

Needs ``forms.responses.readonly``, opt-in via ``GOOGLE_FORMS_ENABLED`` (adding
it by default would force every tenant to reconnect).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

from ..core.exceptions import SourceError

logger = logging.getLogger(__name__)

_DRIVE_API = "https://www.googleapis.com/drive/v3"
_FORMS_API = "https://forms.googleapis.com/v1"
_FORM_MIME = "application/vnd.google-apps.form"

#: Forms are found through Drive (no Forms listing API); bounds that walk.
MAX_FORMS = 25
#: Per form; past this, the most recent responses are used and the caller says so.
MAX_RESPONSES = 500


@dataclass(frozen=True)
class FormRef:
    form_id: str
    title: str


@dataclass(frozen=True)
class FormQuestion:
    question_id: str
    #: The question text: the chart's topic, the only form content kept.
    title: str


@dataclass(frozen=True)
class FormAnswer:
    """One person's answer to one question, with no respondent identity at all."""

    question_id: str
    question_title: str
    text: str
    submitted_at: datetime | None


@dataclass(frozen=True)
class FormResponses:
    form: FormRef
    answers: tuple[FormAnswer, ...] = ()
    truncated: bool = False


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class GoogleFormsReader:
    """Bounded, read-only access to a tenant's Google Forms responses."""

    def __init__(self, token: str, *, timeout: float = 20.0) -> None:
        self._token = token
        self._timeout = timeout

    def _get(self, url: str, params: dict | None = None) -> dict:
        try:
            response = httpx.get(
                url,
                params=params or {},
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"Google Forms request failed: {exc}", cause=exc) from exc

        if response.status_code in (401, 403):
            # Most likely a token from before Forms access: say "reconnect", not 403.
            raise SourceError(
                "Google Forms access was refused. Reconnect Google to grant "
                "response access (the earlier connection did not include it)."
            )
        if response.status_code == 404:
            # Google 404s what a token cannot see, so this is not "deleted".
            raise SourceError("Form not found or not accessible.")
        if response.status_code >= 400:
            raise SourceError(
                f"Google Forms returned {response.status_code} for {url}"
            )
        return response.json() or {}

    def list_forms(self) -> list[FormRef]:
        """Every form this token can see, bounded, via Drive (Forms has no listing)."""
        payload = self._get(
            f"{_DRIVE_API}/files",
            {
                "q": f"mimeType='{_FORM_MIME}' and trashed=false",
                "fields": "files(id,name)",
                "pageSize": MAX_FORMS,
            },
        )
        return [
            FormRef(form_id=f["id"], title=f.get("name") or "Untitled form")
            for f in payload.get("files", [])
        ]

    def fetch_responses(self, form: FormRef) -> FormResponses:
        """Free-text answers to one form, with their questions (choices are already categories)."""
        structure = self._get(f"{_FORMS_API}/forms/{form.form_id}")
        questions = _text_questions(structure)
        if not questions:
            return FormResponses(form=form)

        payload = self._get(
            f"{_FORMS_API}/forms/{form.form_id}/responses",
            {"pageSize": MAX_RESPONSES},
        )
        rows = payload.get("responses", []) or []
        truncated = bool(payload.get("nextPageToken"))

        answers: list[FormAnswer] = []
        for row in rows:
            when = _parse_dt(row.get("lastSubmittedTime"))
            for question_id, answer in (row.get("answers") or {}).items():
                question = questions.get(question_id)
                if question is None:
                    continue
                for value in (answer.get("textAnswers") or {}).get("answers", []):
                    text = (value.get("value") or "").strip()
                    if not text:
                        continue
                    answers.append(
                        FormAnswer(
                            question_id=question_id,
                            question_title=question.title,
                            text=text,
                            submitted_at=when,
                        )
                    )

        return FormResponses(
            form=form, answers=tuple(answers), truncated=truncated
        )


def _text_questions(structure: dict) -> dict[str, FormQuestion]:
    """Free-text questions only, keyed by the id answers arrive under."""
    out: dict[str, FormQuestion] = {}
    for item in structure.get("items", []) or []:
        question = (item.get("questionItem") or {}).get("question") or {}
        question_id = question.get("questionId")
        if not question_id or "textQuestion" not in question:
            continue
        out[question_id] = FormQuestion(
            question_id=question_id,
            title=(item.get("title") or "Untitled question").strip(),
        )
    return out
