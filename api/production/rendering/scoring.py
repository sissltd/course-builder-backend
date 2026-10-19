"""Caption accuracy: how many words a transcription of the finished audio
gets right against the approved narration (1 - word error rate)."""

import re
from decimal import ROUND_HALF_UP, Decimal

WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")


def words(text: str) -> list[str]:
    return WORD.findall(text.lower().replace("’", "'"))


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over words, divided by the reference length."""

    ref, hyp = words(reference), words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i] + [0] * len(hyp)
        for j, hyp_word in enumerate(hyp, start=1):
            current[j] = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (ref_word != hyp_word),
            )
        previous = current
    return previous[-1] / len(ref)


def caption_accuracy_percent(reference: str, hypothesis: str) -> Decimal:
    accuracy = max(0.0, 1.0 - word_error_rate(reference, hypothesis)) * 100
    return Decimal(str(accuracy)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
