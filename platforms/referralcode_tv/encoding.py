"""Reversible repair for the UTF-8-as-Windows-1252 corruption seen in RCTV.

This module never saves to a platform. Only title/description text is eligible;
codes, links, categories and other form values are outside its scope.
"""
from __future__ import annotations

from dataclasses import dataclass


def _byte(char: str) -> int | None:
    # Undefined Windows-1252 bytes were retained as C1 controls by the site.
    if ord(char) <= 255:
        return ord(char)
    try:
        encoded = char.encode("cp1252")
    except UnicodeEncodeError:
        return None
    return encoded[0] if len(encoded) == 1 else None


def _pass(text: str) -> str:
    result = []
    index = 0
    while index < len(text):
        lead = _byte(text[index])
        size = (2 if lead is not None and 0xC2 <= lead <= 0xDF else
                3 if lead is not None and 0xE0 <= lead <= 0xEF else
                4 if lead is not None and 0xF0 <= lead <= 0xF4 else 0)
        chunk = text[index:index + size] if size else ""
        raw = [_byte(char) for char in chunk]
        if size and len(raw) == size and all(b is not None for b in raw):
            try:
                decoded = bytes(raw).decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                pass
            else:
                # Strict UTF-8 guarantees one scalar and rejects overlongs,
                # surrogate code points and invalid continuation bytes.
                result.append(decoded)
                index += size
                continue
        result.append(text[index])
        index += 1
    return "".join(result)


def repair_text(text: str) -> str:
    """Repair reversible sequences, preserving already-valid Unicode verbatim."""
    if "\ufffd" in text:
        raise ValueError("Irrecoverable replacement character: manual review required")
    for _ in range(4):
        repaired = _pass(text)
        if repaired == text:
            return text
        text = repaired
    if _pass(text) != text:
        raise ValueError("Encoding corruption exceeds bounded repair depth")
    return text


def needs_repair(text: str) -> bool:
    return "\ufffd" in text or repair_text(text) != text


@dataclass(frozen=True)
class TextRepair:
    title_before: str
    body_before: str
    title_after: str
    body_after: str

    @classmethod
    def prepare(cls, title: str, body: str) -> "TextRepair":
        return cls(title, body, repair_text(title), repair_text(body))

    def verify_source(self, title: str, body: str) -> None:
        if (title, body) != (self.title_before, self.body_before):
            raise ValueError("Form changed since preparation: recapture before editing")
