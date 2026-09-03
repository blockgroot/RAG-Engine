"""Google Forms responses — read live, deliberately NEVER indexed.

Not a ``SourceAdapter``, and that is the whole design. Every other source here
gets chunked and embedded so anyone in the org can ask questions of it. A
survey response must not go into that corpus: it would make
"what did Ada say about management?" an answerable question, which is the exact
opposite of what an anonymous survey promises. So responses are read, classified
once into a sentiment label (``app/insights/sentiment.py``), and the RESPONSE
TEXT IS DISCARDED — only the label and the topic are stored.

Same shape as ``app/githublive/``: bounded live reads, no vectors.

**Scope.** Reading responses needs
``https://www.googleapis.com/auth/forms.responses.readonly``, which is NOT in
this codebase's default Google scopes: adding it would force every existing
tenant to reconnect. It is opt-in through ``GOOGLE_FORMS_ENABLED`` (see
``GoogleSettings``), and a token without it fails with a message that says to
reconnect rather than a bare 403.
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

#: Forms are found through Drive (the Forms API has no "list my forms"), so
#: this bounds that walk the same way ``google_drive`` bounds its own.
MAX_FORMS = 25
#: Per form. A survey with more responses than this is summarised from the most
#: recent ones, and the caller says so.
MAX_RESPONSES = 500


@dataclass(frozen=True)
class FormRef:
    form_id: str
    title: str


@dataclass(frozen=True)
class FormQuestion:
    question_id: str
    #: The question text. This is what becomes a chart's topic, so it is the
    #: one piece of form *content* that is kept.
    title: str


@dataclass(frozen=True)
class FormAnswer:
    """One person's answer to one question.

    Carries no respondent identity at all — not even an opaque id. There is no
    per-person view of this data anywhere in the product, so storing a handle
    would only create the possibility of one.
    """

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
            # The overwhelmingly likely cause is a token issued before Forms
            # access was enabled. Saying "reconnect" is actionable; "403" is
            # not, and would send someone hunting a permissions bug that isn't
            # one.
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
        """Every form this token can see, bounded.

        Through Drive, because the Forms API has no listing endpoint of its
        own — only ``forms.get`` and ``forms.responses.list`` by id.
        """
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
        """Free-text answers to one form, with the questions they answer.

        Only free text is returned. A multiple-choice answer is already a
        category and needs no model to classify it; running one over "Yes"
        would spend a request to learn nothing.
        """
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
    """Free-text questions only, keyed by the id answers arrive under.

    A choice question is skipped: its answers are already categories, so
    classifying them would spend a model request to rediscover the options.
    """
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
