"""Quality checks for locally generated Korean report text.

Two failure modes show up with a local multilingual model writing long Korean
prose, both observed in saved reports:

* **Han characters.** Qwen slips single Chinese words into Korean sentences
  ("영업외 손실所致", "價値", "贪婪"). Every report written so far contains a few.
* **Degenerate repetition.** A decoding loop emits one fragment thousands of
  times; the 000660 report carries "哪怕" repeated ~3,000 times, which buries the
  rest of the section.

Detection lives here so the run can report both as run-quality metrics next to
structured-output fallbacks, and so the report writer can collapse a repetition
loop into something readable while saying exactly what it removed. The raw model
output is kept untouched in ``final_state.json``.
"""

from __future__ import annotations

import re

# CJK ideographs: Unified, Extension A, and the compatibility block. Hangul, Latin
# and the fullwidth punctuation Korean text legitimately uses are not matched.
HAN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")

# A fragment of 1-40 characters repeated back-to-back. Ten repeats is far past any
# legitimate Korean phrasing, and markdown tables never repeat a run that long
# because each row carries different text.
_REPEAT_RE = re.compile(r"(.{1,40}?)\1{9,}", re.DOTALL)

_MIN_REPEATS = 10


def han_characters(text: str) -> list[str]:
    """Distinct Han characters in ``text``, sorted."""
    return sorted(set(HAN_RE.findall(text or "")))


def repetitions(text: str) -> list[tuple[str, int]]:
    """Degenerate repeats as ``(fragment, repeat count)``, longest run first."""
    found = [(m.group(1), len(m.group(0)) // len(m.group(1))) for m in _REPEAT_RE.finditer(text or "")]
    return sorted(found, key=lambda p: -p[1])


def collapse_repetition(text: str) -> tuple[str, list[tuple[str, int]]]:
    """Trim degenerate repeats to two copies, marking what was removed.

    The marker is written into the text rather than the run silently dropping
    content: a reader sees that the model looped and by how much.
    """
    removed: list[tuple[str, int]] = []

    def _replace(match: re.Match) -> str:
        fragment = match.group(1)
        count = len(match.group(0)) // len(fragment)
        removed.append((fragment, count))
        return f"{fragment * 2}… [반복 {count:,}회 축약: {fragment!r}]"

    return _REPEAT_RE.sub(_replace, text or ""), removed


def scan(texts: dict[str, str]) -> dict:
    """Per-section quality summary for the run stats."""
    han: dict[str, int] = {}
    loops: dict[str, list] = {}
    for key, value in texts.items():
        hits = HAN_RE.findall(value or "")
        if hits:
            han[key] = len(hits)
        runs = repetitions(value or "")
        if runs:
            loops[key] = [{"fragment": f, "count": c} for f, c in runs[:5]]
    out = {"han_characters": han, "repetition_loops": loops}
    out["han_total"] = sum(han.values())
    out["han_distinct"] = han_characters("".join(texts.values()))
    return out
