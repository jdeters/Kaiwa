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

# particle uses labeled by the correction call (prompts.CORRECTION_PROMPT) → grammar
# concepts; these particles have several meanings that the text alone can't tell apart
PARTICLE_USES = {
    "ni.destination": "particle.ni.destination", "ni.time": "particle.ni.time",
    "ni.target": "particle.ni.target", "de.location": "particle.de.location",
    "de.means": "particle.de.means", "to.and": "particle.to.and", "to.with": "particle.to.with",
    "kara.origin": "particle.kara.origin", "kara.time": "kara.made.time",
    "no.possession": "no.possession", "no.affiliation": "no.affiliation", "no.modifier": "no.modifier",
}


@dataclass
class Observation:
    kind: str      # "word" | "grammar"
    item: str      # Profile.word_id() or a grammar concept id
    outcome: str
    surface: str   # as it appeared in the (normalized) message


def _hira(s: str) -> str:
    return kata_to_hira(re.sub(r"[\s、。！？!?,.]", "", s or ""))


def _forms(fragment: str) -> set:
    """A correction fragment as written and as read: "多い" is also おおい, so it
    can be compared with token readings."""
    return {_hira(fragment), _hira("".join(t.kana for t in morph.tokenize(fragment)))} - {""}


def observe(text: str, profile: Profile, prev_partner: str = "", errors=(), hint: bool = False,
            particles=(), uncertain=()) -> list:
    """particles: [{"particle", "use"}] in order, from the correction call — resolves
    に/で/と/から/の to a specific concept. uncertain: what possibly-misheard words
    probably were (spoken messages); they're left out, since what was said is unknown.
    Spelling slips count as the word meant (おおいしです would otherwise read as
    多い + し) when the fix parses as whole words; otherwise they're left out
    (かるまで fixed to くるまで still reads as 来る + まで)."""
    errors, uncertain, fixed = list(errors or ()), list(uncertain or ()), []
    for e in errors:
        if e.get("category") == "spelling" and e.get("wrong"):
            if e["wrong"] in text and e.get("right"):
                text = text.replace(e["wrong"], e["right"], 1)
                fixed.append(e["right"])
            else:   # spelled differently in the normalized text: leave that stretch out
                uncertain.append(e["wrong"])
    errors = [e for e in errors if e.get("category") != "spelling"]
    toks = morph.tokenize(text)
    hits = grammar.detect(toks)
    function = {i for h in hits for i in h.function}   # grammar tokens aren't vocabulary
    partner_words = {w for *_, w in profile.word_spans(morph.tokenize(prev_partner))}
    # a span is an error if the correction's "wrong" fragment has it and the "right" one
    # doesn't — compared by reading too, since fragments may be in kanji (多い → 大きい)
    flagged = [(_forms(e.get("wrong", "")), _forms(e.get("right", ""))) for e in errors]

    def outcome(kana: str, word: str | None = None) -> str:
        if hint:
            return "hint"
        if kana and any(any(kana in w for w in ws) and not any(kana in r for r in rs)
                        for ws, rs in flagged):
            return "error"
        if word and word in partner_words:
            return "echo"
        return "independent"

    unsure = [_hira(u) for u in uncertain if u]
    labeled: dict = {}
    for p in particles or ():
        concept = PARTICLE_USES.get(p.get("use", ""))
        labeled.setdefault(p.get("particle", ""), []).append(concept)

    # tokens inside a possibly-misheard stretch, located by position — in the
    # text as written or in its kana reading (a substring test alone would also
    # catch, say, every に in the sentence)
    unsure_idx = set()
    for form in (lambda t: _hira(t.surface), lambda t: _hira(t.kana)):
        spans, pos = [], 0
        for t in toks:
            k = form(t)
            spans.append((pos, pos + len(k)))
            pos += len(k)
        whole = "".join(form(t) for t in toks)
        for u in unsure:
            start = whole.find(u) if u else -1
            while start >= 0:
                unsure_idx |= {i for i, (a, b) in enumerate(spans)
                               if b > a and a < start + len(u) and b > start}
                start = whole.find(u, start + 1)

    bounds = {t.start for t in toks} | {t.end for t in toks}
    for right in fixed:
        a = text.find(right)
        if a >= 0 and not (a in bounds and a + len(right) in bounds):
            unsure_idx |= {i for i, t in enumerate(toks) if t.start < a + len(right) and t.end > a}

    out, seen = [], set()
    for i, j, wid in profile.word_spans(toks):
        if any(k in unsure_idx or k in function for k in range(i, j)):
            continue
        if ("word", wid) not in seen:
            seen.add(("word", wid))
            out.append(Observation("word", wid, outcome("".join(t.kana for t in toks[i:j]), wid),
                                   "".join(t.surface for t in toks[i:j])))
    for h in hits:
        if len(h.options) > 1:
            # に/で/と/から/の: use the labeled meaning, matched in order of appearance
            queue = labeled.get(h.label.removeprefix("particle "), [])
            concept = queue.pop(0) if queue else None
            known = [concept] if concept in h.options and concept in profile.grammar else []
        else:
            known = [o for o in h.options if o in profile.grammar]
        # は/ね/よ map to the basic copula concept: not worth tracking
        if not known or (h.label.startswith("particle") and known[0] == "copula.desu"):
            continue
        if ("grammar", known[0]) in seen:
            continue
        span = toks[h.i:min(h.j, len(toks))]
        if any(k in unsure_idx for k in range(h.i, min(h.j, len(toks)))):   # guessed: unknown
            continue
        seen.add(("grammar", known[0]))
        out.append(Observation("grammar", known[0], outcome("".join(x.kana for x in span)),
                               "".join(x.surface for x in span)))
    return out
