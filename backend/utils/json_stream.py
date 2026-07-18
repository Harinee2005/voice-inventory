"""
Incremental extraction of one string field from streaming JSON.

ARIA's structured output starts with {"message": "..."} — message is the
FIRST field of ARIAResult — so its characters arrive within the first few
output tokens. MessageFieldExtractor decodes them as they stream in,
handling escape sequences split across chunk boundaries.
"""

from __future__ import annotations

import re

_OPEN_RE_TEMPLATE = r'"{field}"\s*:\s*"'
_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", '"': '"',
    "\\": "\\", "/": "/", "b": "\b", "f": "\f",
}


class MessageFieldExtractor:
    """Feed raw JSON text deltas; get back newly decoded field text.

    Usage:
        ex = MessageFieldExtractor()
        for delta in stream:
            new_text = ex.feed(delta)   # "" until the field opens / after it closes
    """

    def __init__(self, field: str = "message") -> None:
        self._open_re = re.compile(_OPEN_RE_TEMPLATE.format(field=re.escape(field)))
        self._buf = ""
        self._value_start: int | None = None
        self._emitted = 0
        self._done = False

    @property
    def done(self) -> bool:
        return self._done

    def feed(self, delta: str) -> str:
        if self._done or not delta:
            return ""
        self._buf += delta

        if self._value_start is None:
            m = self._open_re.search(self._buf)
            if not m:
                return ""
            self._value_start = m.end()

        # Decode from the value start each time; the message is 1–2 sentences,
        # so the rescan cost is negligible. Stop at an incomplete trailing
        # escape (it completes on the next feed) or the closing quote.
        segment = self._buf[self._value_start:]
        chars: list[str] = []
        i = 0
        while i < len(segment):
            c = segment[i]
            if c == "\\":
                if i + 1 >= len(segment):
                    break  # escape split across chunks — resume next feed
                nxt = segment[i + 1]
                if nxt == "u":
                    if i + 6 > len(segment):
                        break
                    try:
                        chars.append(chr(int(segment[i + 2:i + 6], 16)))
                    except ValueError:
                        pass
                    i += 6
                    continue
                chars.append(_ESCAPES.get(nxt, nxt))
                i += 2
                continue
            if c == '"':
                self._done = True
                break
            chars.append(c)
            i += 1

        decoded = "".join(chars)
        new_text = decoded[self._emitted:]
        self._emitted = len(decoded)
        return new_text
