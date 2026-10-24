"""Deterministic checks of model output against source text (spec §8): quotes exist, numbers are not invented.

No model is involved here: these checks are what makes a model's answer trustworthy.
"""
import re
from decimal import Decimal
from difflib import SequenceMatcher

NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
# "256k", "7B", "$1.5 billion", "12 mn": the same amount as "256,000", "7,000,000,000"...
SCALED_NUMBER = re.compile(
    r'(\d[\d,]*(?:\.\d+)?)(?:([kKMBT])\b|\s?(thousand|million|billion|trillion|mn|bn)\b)?', re.IGNORECASE,
)
SCALES = {'k': 3, 'thousand': 3, 'm': 6, 'mn': 6, 'million': 6, 'b': 9, 'bn': 9, 'billion': 9, 't': 12, 'trillion': 12}
WORD = re.compile(r'[a-z][a-z0-9\-]{3,}')
# Names with digits in them: CVE-2026-109618, PHP2Shell, K3.1, a16z, gpt-6.1. Changing one digit makes another
# product or bug, and the plain number check misses that when the new digit occurs somewhere else in the text
CODE = re.compile(r'\b(?=[\w.\-]*[a-z])(?=[\w.\-]*\d)[a-z0-9][\w.\-]*[a-z0-9]\b')
# "$3.25M", "30B", "2K", "40%": amounts, which the number check covers (as 3250000...), not names
AMOUNT = re.compile(r'[\d.,]+(?:k|m|b|t|mn|bn)')
# Quote marks are dropped, not unified: models requote "horrifying" as 'horrifying'
QUOTE_CHARS = str.maketrans({'‘': None, '’': "'", '“': None, '”': None, '"': None, '–': '-', '—': '-',
                              '‑': '-', ' ': ' '})
# A quote shortened with an ellipsis: "Proposition 40 ... would level a tax"
ELLIPSIS = re.compile(r'\s*(?:\.{3,}|…)\s*')
MIN_FRAGMENT = 12  # shorter pieces of an elided quote prove nothing on their own
STOPWORDS = {
    'that', 'this', 'with', 'from', 'have', 'has', 'been', 'will', 'were', 'which', 'their', 'there', 'about',
    'into', 'than', 'they', 'also', 'more', 'said', 'says', 'after', 'over', 'such', 'only', 'other', 'would',
}


QUOTING_APOSTROPHE = re.compile(r"(?<![a-z0-9])'|'(?![a-z0-9])")  # 'quoted' but not don't or Meta's


def normalize(text: str) -> str:
    text = re.sub(r'\s+', ' ', text.translate(QUOTE_CHARS)).strip().lower()
    return QUOTING_APOSTROPHE.sub('', text)


def numbers(text: str) -> set[str]:
    """Numbers as bare digit strings: "$1,500.50" -> "1500.5", "v2.5" -> "2.5", "40%" -> "40",
    "256k" -> "256000", "$1.5 billion" -> "1500000000"."""
    found = set()
    for digits, letter, word in SCALED_NUMBER.findall(text):
        value = Decimal(digits.replace(',', '').rstrip('.'))
        if scale := letter or word:
            value = value.scaleb(SCALES[scale.lower()])
        found.add(f'{value.normalize():f}' if value != value.to_integral() else str(int(value)))
    return found


def unsupported_numbers(claim: str, source: str) -> set[str]:
    """Numbers in the claim that never occur in the source: the typical hallucination of small models."""
    return numbers(claim) - numbers(source)


def rounded_numbers(claim: str, source: str) -> set[str]:
    """Numbers of the claim not in the source verbatim but as a source number rounded to 2+ significant digits:
    "€3.25M" for €3,252,618, "over 10,000" for 10,909. Reported apart from invented ones."""
    exact = numbers(source)
    candidates = [Decimal(n) for n in exact]
    return {n for n in numbers(claim) - exact if any(_rounds_to(Decimal(n), c) for c in candidates)}


def _rounds_to(value: Decimal, source: Decimal) -> bool:
    if value <= 0 or source < 100:
        return False
    digits = len(str(int(source)))
    for significant in range(2, digits):
        step = Decimal(10) ** (digits - significant)
        if value in ((source / step).to_integral() * step, (source // step) * step):
            return True
    return False


def codes(text: str) -> set[str]:
    return {code for code in CODE.findall(normalize(text)) if not AMOUNT.fullmatch(code)}


def claim_problems(claim: str, quote: str, source: str) -> list[str]:
    """Why a model's "supported" verdict cannot be trusted; empty if it can.
    The model checks meaning, this checks what small models get wrong: an invented quote, a changed number or
    a changed name (it confirms "$1101 million" quoting "$550 million")."""
    problems = []
    if not quote_in_text(quote, source):
        problems.append('quote not in articles')
    if missing := unsupported_numbers(claim, source):
        problems.append(f'numbers not in articles: {sorted(missing)}')
    if missing := codes(claim) - codes(source):
        problems.append(f'names not in articles: {sorted(missing)}')
    return problems


def quote_in_text(quote: str, text: str, min_ratio: float = 0.9) -> bool:
    """The quote is (almost) verbatim in the text: tolerates quote marks, dashes, whitespace and ellipses.

    An elided quote passes when every piece between the ellipses is in the text, in order.
    """
    text = normalize(text)
    fragments = [f for f in (normalize(part) for part in ELLIPSIS.split(quote)) if f]
    if not fragments:
        return False
    long_enough = [f for f in fragments if len(f) >= MIN_FRAGMENT] or fragments[:1]
    position = 0
    for fragment in long_enough:
        found = _find(fragment, text, position, min_ratio)
        if found is None:
            return False
        position = found
    return True


def _find(fragment: str, text: str, start: int, min_ratio: float) -> int | None:
    """End position of the fragment in text[start:], exact or near-exact; None if absent."""
    exact = text.find(fragment, start)
    if exact >= 0:
        return exact + len(fragment)
    match = SequenceMatcher(None, text, fragment, autojunk=False).find_longest_match(start, len(text), 0, len(fragment))
    return match.a + match.size if match.size / len(fragment) >= min_ratio else None


def content_words(text: str) -> set[str]:
    return set(WORD.findall(normalize(text))) - STOPWORDS


def same_statement(expected: str, candidate: str, min_overlap: float = 0.5) -> bool:
    """Rough match of two statements of one fact: the expected numbers are all there and half the words."""
    if not numbers(expected) <= numbers(candidate):
        return False
    words = content_words(expected)
    return not words or len(words & content_words(candidate)) / len(words) >= min_overlap
