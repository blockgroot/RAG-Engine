"""Identifiers found by their format alone: Aadhaar, Virtual ID, PAN, card,
mobile, IFSC and UPI.

Bank account numbers and order ids are deliberately NOT here: an account number
is 9-18 digits with no check digit, the same shape as an order id, a timestamp
or an invoice number, so no format can tell them apart.

Every numeric kind is confirmed by its check digit or validity rules
(python-stdnum, phonenumbers) before it counts, so a 12-digit order id is not
an Aadhaar unless its Verhoeff digit happens to fit. A false match costs little:
the value is restored in the answer, only the model sees the placeholder.
"""

from __future__ import annotations

import re

from .base import Found, IdentifierDetector

# Digits never continue across these edges: a match inside a longer number,
# a word, or a decimal ("1712345678.123456") is not an identifier.
_START = r"(?<![\w\-.@])"
_END = r"(?![\w@]|[\-.,]\d)"

_VID = re.compile(_START + r"[2-9]\d{3}(?:[ \-]?\d{4}){3}" + _END)
_AADHAAR = re.compile(_START + r"[2-9]\d{3}[ \-]?\d{4}[ \-]?\d{4}" + _END)
_CARD = re.compile(
    _START
    + r"(?:\d{13,19}|\d{4}(?:[ \-]\d{4}){2}[ \-]\d{1,7}|\d{4}[ \-]\d{6}[ \-]\d{5})"
    + _END
)
_PAN = re.compile(r"(?<![\w])[A-Z]{5}\d{4}[A-Z](?!\w)")
_IFSC = re.compile(r"(?<![\w])[A-Z]{4}0[A-Z0-9]{6}(?!\w)")
# A UPI handle has no dot after the "@" (that would be an email domain); a
# full stop ending the sentence is not one.
_UPI = re.compile(r"(?<![\w.\-@])[\w.\-]{2,256}@[A-Za-z]{2,64}(?![\w\-@]|\.\w)")

_DIGITS = re.compile(r"\D")


class PatternDetector(IdentifierDetector):
    def find(self, text: str) -> list[Found]:
        if not text:
            return []
        from stdnum import luhn
        from stdnum.in_ import aadhaar, pan, vid

        found: list[Found] = []

        def take(kind: str, pattern: re.Pattern, valid, key) -> None:
            for m in pattern.finditer(text):
                raw = m.group(0)
                if valid(raw) and not _overlaps(found, m.start(), m.end()):
                    found.append(Found(m.start(), m.end(), kind, key(raw)))

        digits = lambda raw: _DIGITS.sub("", raw)  # noqa: E731
        # Order is priority: a 16-digit Virtual ID is checked before a card,
        # and a 12-digit Aadhaar before anything shorter claims its digits.
        take("VID", _VID, lambda r: vid.is_valid(digits(r)), digits)
        take("AADHAAR", _AADHAAR, lambda r: aadhaar.is_valid(digits(r)), digits)
        take("CARD", _CARD, lambda r: luhn.is_valid(digits(r)), digits)
        take("PAN", _PAN, pan.is_valid, str.upper)
        take("IFSC", _IFSC, lambda r: True, str.upper)
        take("UPI", _UPI, lambda r: True, str.lower)
        for start, end, key in _phones(text):
            if not _overlaps(found, start, end):
                found.append(Found(start, end, "PHONE", key))
        return sorted(found, key=lambda f: f.start)


def _phones(text: str) -> list[tuple[int, int, str]]:
    """Mobile numbers (Indian by default), and any number written with ``+``.

    Landlines without a country code are left alone: ``1712345678`` is a valid
    Indian landline to the library and also every Unix timestamp this decade.
    """
    import phonenumbers
    from phonenumbers import Leniency, PhoneNumberMatcher, PhoneNumberType

    mobile = (PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE)
    out = []
    for m in PhoneNumberMatcher(text, "IN", leniency=Leniency.VALID):
        before, after = text[max(0, m.start - 2):m.start], text[m.end:m.end + 2]
        if re.search(r"\d[.,]$", before) or re.match(r"[.,]\d", after):
            continue
        written_international = m.raw_string.lstrip().startswith("+")
        if not written_international and phonenumbers.number_type(m.number) not in mobile:
            continue
        key = phonenumbers.format_number(m.number, phonenumbers.PhoneNumberFormat.E164)
        out.append((m.start, m.end, key))
    return out


def _overlaps(found: list[Found], start: int, end: int) -> bool:
    return any(f.start < end and start < f.end for f in found)
