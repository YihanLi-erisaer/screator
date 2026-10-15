"""Remove sentences containing links before translating source descriptions."""
from __future__ import annotations

import re


_LINK = re.compile(r"(?:https?://|ftp://|www\.)[^\s<>，。！？；]+", re.IGNORECASE)
_SENTENCE_END = re.compile(r'''[。！？]+[”’」』）)\]"']*|[.!?]+[”’」』）)\]"']*(?=\s|$)''')


def remove_link_sentences(description: str) -> str:
    """Treat newlines as boundaries too, so standalone links remove only their line."""
    if not _LINK.search(description):
        return description.strip()
    lines = []
    for line in description.splitlines():
        links = [(match.start(), match.start() + len(match.group().rstrip(".,;:!?)]}\"'”’")))
                 for match in _LINK.finditer(line)]
        if not links:
            lines.append(line)
            continue
        # Hide punctuation inside URLs when finding sentence boundaries. Keep
        # trailing sentence punctuation visible (including after a bare URL).
        masked = list(line)
        for start, end in links:
            masked[start:end] = "x" * (end - start)
        start = 0
        kept = []
        boundaries = [match.end() for match in _SENTENCE_END.finditer("".join(masked))]
        for end in [*boundaries, len(line)]:
            if not any(link_start < end and link_end > start for link_start, link_end in links):
                sentence = line[start:end]
                if sentence.strip():
                    kept.append(sentence)
            start = end
        if kept:
            lines.append("".join(kept).strip())
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "\n".join(lines)).strip()
