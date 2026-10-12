"""What the learner produces: which known words and grammar a message uses, and how.

Outcomes, strongest evidence first:
  independent — used correctly, unprompted
  echo        — the partner had just used the same word (may be copying)
  hint        — the message came from a suggested reply
  error       — the correction flagged it
Only `independent` counts as evidence the learner can say it.

Learner messages are mostly kana (romaji typing), which the tokenizer misreads,
so observe() expects the message re-spelled with normal kanji, mistakes kept
(the correction call returns this as "normalized").
"""
import re
from dataclasses import dataclass

from ..jp import kata_to_hira
from . import grammar, morph
from .profile import Profile

OUTCOMES = ("independent", "echo", "hint", "error")


@dataclass
class Observation:
    kind: str      # "word" | "grammar"
    item: str      # Profile.word_id() or a grammar concept id
    outcome: str
    surface: str   # as it appeared in the (normalized) message


def _hira(s: str) -> str:
    return kata_to_hira(re.sub(r"[\s、。！？!?,.]", "", s or ""))


def observe(text: str, profile: Profile, prev_partner: str = "", errors=(), hint: bool = False) -> list:
    toks = morph.tokenize(text)
    hits = grammar.detect(toks)
    function = {i for h in hits for i in h.function}   # grammar tokens aren't vocabulary
    partner_words = {profile.word_id(t) for t in morph.tokenize(prev_partner)} - {None}
    # a span is an error if the correction's "wrong" fragment has it and the "right" one doesn't
    flagged = [(_hira(e.get("wrong", "")), _hira(e.get("right", ""))) for e in errors or ()]

    def outcome(kana: str, word: str | None = None) -> str:
        if hint:
            return "hint"
        if kana and any(kana in w and kana not in r for w, r in flagged):
            return "error"
        if word and word in partner_words:
            return "echo"
        return "independent"

    out, seen = [], set()
    for i, t in enumerate(toks):
        wid = None if i in function else profile.word_id(t)
        if wid and ("word", wid) not in seen:
            seen.add(("word", wid))
            out.append(Observation("word", wid, outcome(t.kana, wid), t.surface))
    for h in hits:
        # only unambiguous constructions: に/で/と/から/の have several meanings,
        # and は/ね/よ map to the basic copula concept
        known = [o for o in h.options if o in profile.grammar]
        if len(h.options) != 1 or not known or (h.label.startswith("particle") and known[0] == "copula.desu"):
            continue
        if ("grammar", known[0]) in seen:
            continue
        seen.add(("grammar", known[0]))
        span = toks[h.i:min(h.j, len(toks))]
        out.append(Observation("grammar", known[0], outcome("".join(x.kana for x in span)),
                               "".join(x.surface for x in span)))
    return out
