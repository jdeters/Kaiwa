"""Check a Japanese sentence against a learner profile.

Every token must be licensed: content words by the vocabulary list (or the
closed-class list), grammar by an unlocked concept. Multi-word entries from the
learner's decks (まってください) license their tokens whole, even when their
grammar hasn't been taught yet. Proper nouns pass but are reported.
"""
from dataclasses import dataclass, field

from . import grammar, morph
from .profile import Profile

_SKIP_POS = {"補助記号", "記号", "空白"}


@dataclass
class Violation:
    kind: str       # "vocab" | "grammar"
    text: str       # offending span as written
    detail: str     # explanation for the repair prompt / report

    def __str__(self):
        return f"[{self.kind}] 「{self.text}」 {self.detail}"


@dataclass
class Result:
    text: str
    violations: list = field(default_factory=list)
    proper_nouns: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _phrase_exempt(toks: list, profile: Profile) -> set:
    """Indices of tokens covered by a known deck entry matched by kana.

    A match must start and end on token boundaries (いま must not match the
    start of います) and contain a content word — a grammar-only card like
    でした must not unlock its grammar."""
    kana, spans, pos = "", [], 0
    for t in toks:
        k = t.kana if t.pos1 not in _SKIP_POS else ""
        spans.append((pos, pos + len(k)))
        kana += k
        pos += len(k)
    starts = {a: i for i, (a, b) in enumerate(spans) if b > a}
    ends = {b: i for i, (a, b) in enumerate(spans) if b > a}
    exempt = set()
    for ph in profile.phrases:
        start = kana.find(ph)
        while start >= 0:
            end = start + len(ph)
            if start in starts and end in ends:
                covered = range(starts[start], ends[end] + 1)
                if any(toks[i].content for i in covered):
                    exempt.update(covered)
            start = kana.find(ph, start + 1)
    return exempt


def validate(text: str, profile: Profile, concepts: dict | None = None) -> Result:
    concepts = concepts or grammar.load_concepts()
    toks = morph.tokenize(text)
    exempt = _phrase_exempt(toks, profile)
    for i, j, _ in profile.word_spans(toks):   # a known word the tokenizer splits (何曜 + 日)
        if j - i > 1:
            exempt.update(range(i, j))
    res = Result(text)

    function = set()
    for h in grammar.detect(toks):
        function.update(h.function)
        if all(k in exempt for k in range(h.i, min(h.j, len(toks)))):
            continue
        span = text[toks[h.i].start:toks[min(h.j, len(toks)) - 1].end]
        if not h.options:
            res.violations.append(Violation("grammar", span, f"uses {h.label}, which isn't in the learner's grammar"))
            continue
        if any(o in profile.grammar for o in h.options):
            continue
        names = " / ".join(concepts[o]["name"] for o in h.options if o in concepts)
        res.violations.append(Violation("grammar", span, f"uses {h.label} ({names}), not learned yet"))

    for i, t in enumerate(toks):
        if i in exempt or i in function or t.pos1 in _SKIP_POS or not t.content:
            continue
        if t.pos2 == "固有名詞":
            res.proper_nouns.append(t.surface)
            continue
        # single-kanji suffix of a place name (富士+山, 浅草+寺): unidic-lite
        # splits these and often misreads them (やま for さん)
        if i > 0 and toks[i - 1].pos2 == "固有名詞" and t.pos1 == "名詞" and len(t.surface) == 1:
            if res.proper_nouns and i - 1 not in exempt:
                res.proper_nouns[-1] += t.surface
            continue
        if not profile.known(t):
            res.violations.append(Violation("vocab", t.surface, f"({t.lemma}) is not a word the learner knows"))
    return res
