"""Recognize DOI/ISBN queries and match them exactly against stored item fields."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import unquote

_DOI_URL_PREFIX = re.compile(r"^(?:https?://)?(?:dx\.|www\.)?doi\.org/", re.IGNORECASE)
_DOI_LABEL_PREFIX = re.compile(r"^doi\s*:\s*", re.IGNORECASE)
_DOI_BODY = re.compile(r"10\.\d{4,9}/\S+")
_EXTRA_DOI_LINE = re.compile(r"^\s*doi\s*:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_ISBN_LABEL_PREFIX = re.compile(r"^isbn(?:[- ]?1[03])?\s*[:：]?\s*", re.IGNORECASE)
_ISBN_QUERY_BODY = re.compile(r"[0-9][0-9\- ]{7,}[0-9Xx]")
_ISBN_TOKEN = re.compile(r"(?<![0-9A-Za-z])[0-9][0-9\-]{8,15}[0-9Xx](?![0-9A-Za-z])")


@dataclass(frozen=True)
class Identifier:
    """A DOI or ISBN extracted from a whole search query."""

    kind: Literal["doi", "isbn"]
    # Normalized form reported to the caller: lowercase DOI, or digits-only ISBN-13
    # (the ISBN-10 digits when no ISBN-13 equivalent exists).
    value: str
    # Native q strings that together retrieve every candidate item.
    queries: tuple[str, ...]
    # Normalized spellings a stored value may take and still be the same identifier.
    accepted: frozenset[str]

    @property
    def fields(self) -> list[str]:
        return ["DOI", "extra"] if self.kind == "doi" else ["ISBN"]


def normalize_doi(text: str) -> str | None:
    """Return the lowercase DOI inside text (bare, doi: or doi.org URL form), or None."""
    text = text.strip()
    from_url = False
    if match := _DOI_URL_PREFIX.match(text):
        text, from_url = text[match.end() :], True
    elif match := _DOI_LABEL_PREFIX.match(text):
        text = text[match.end() :]
    if from_url:
        text = unquote(text.split("#", 1)[0].split("?", 1)[0])
    return text.lower() if _DOI_BODY.fullmatch(text) else None


def _isbn_check_ok(digits: str) -> bool:
    if len(digits) == 13:
        if not digits.isdigit():
            return False
        total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(digits[:12]))
        return (10 - total % 10) % 10 == int(digits[12])
    if len(digits) == 10 and digits[:9].isdigit():
        total = sum((10 - i) * int(c) for i, c in enumerate(digits[:9]))
        last = 10 if digits[9] in "Xx" else int(digits[9]) if digits[9].isdigit() else -1
        return (total + last) % 11 == 0
    return False


def _isbn13_from_10(digits: str) -> str:
    body = "978" + digits[:9]
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(body))
    return body + str((10 - total % 10) % 10)


def _isbn10_from_13(digits: str) -> str | None:
    if not digits.startswith("978"):
        return None
    body = digits[3:12]
    check = (-sum((10 - i) * int(c) for i, c in enumerate(body))) % 11
    return body + ("X" if check == 10 else str(check))


def _isbn_forms(digits: str) -> frozenset[str]:
    """Both spellings (ISBN-10 and ISBN-13) of a checksum-valid ISBN, in upper case."""
    digits = digits.upper()
    if len(digits) == 10:
        return frozenset({digits, _isbn13_from_10(digits)})
    ten = _isbn10_from_13(digits)
    return frozenset({digits, ten} if ten else {digits})


def parse_identifier(query: str) -> Identifier | None:
    """Recognize a query that is, as a whole, one DOI or one ISBN-10/13."""
    text = query.strip()
    if doi := normalize_doi(text):
        return Identifier("doi", doi, (doi,), frozenset({doi}))

    labelled = _ISBN_LABEL_PREFIX.match(text)
    body = text[labelled.end() :] if labelled else text
    if not _ISBN_QUERY_BODY.fullmatch(body):
        return None
    digits = re.sub(r"[\- ]", "", body).upper()
    if len(digits) not in (10, 13):
        return None
    valid = _isbn_check_ok(digits)
    # A bare 10/13-digit number is only an ISBN when its check digit agrees; an
    # explicit "ISBN" label is trusted so that a mistyped or oddly stored value
    # still gets an exact (unconverted) lookup.
    if not valid and not labelled:
        return None
    accepted = _isbn_forms(digits) if valid else frozenset({digits})
    value = _isbn13_from_10(digits) if valid and len(digits) == 10 else digits
    # Zotero stores ISBNs hyphenated and its LIKE search treats % as a wildcard, so
    # d%i%g%i%t%s finds the value however it is grouped.
    queries = tuple("%".join(form) for form in sorted(accepted, key=len, reverse=True))
    return Identifier("isbn", value, queries, accepted)


def stored_isbns(value: str) -> set[str]:
    """Normalized ISBN spellings found in an ISBN field (may hold several ISBNs)."""
    found: set[str] = set()
    candidates = [m.group() for m in _ISBN_TOKEN.finditer(value)]
    # A single spaced ISBN such as "978 0 262 03561 3".
    candidates.append(_ISBN_LABEL_PREFIX.sub("", value.strip()))
    for candidate in candidates:
        digits = re.sub(r"[\- ]", "", candidate).upper()
        if len(digits) in (10, 13) and digits[:-1].isdigit():
            found.add(digits)
            if len(digits) == 10 and _isbn_check_ok(digits):
                found.add(_isbn13_from_10(digits))
    return found


def matching_field(item: Mapping[str, Any], identifier: Identifier) -> tuple[str, str] | None:
    """Return (field, stored value) when the item holds exactly this identifier."""
    data = item.get("data")
    if not isinstance(data, Mapping):
        return None
    if identifier.kind == "doi":
        stored = data.get("DOI")
        if isinstance(stored, str) and normalize_doi(stored) in identifier.accepted:
            return "DOI", stored
        extra = data.get("extra")
        if isinstance(extra, str):
            for line in _EXTRA_DOI_LINE.finditer(extra):
                if normalize_doi(line.group(1)) in identifier.accepted:
                    return "extra", line.group(1)
        return None
    stored = data.get("ISBN")
    if isinstance(stored, str) and stored_isbns(stored) & identifier.accepted:
        return "ISBN", stored
    return None
