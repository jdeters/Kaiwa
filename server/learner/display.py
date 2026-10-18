"""Render validated Japanese for the learner's eyes.

Generation and validation work on normal kanji text; the learner sees kanji
only for words whose kanji spelling they've learned (profile.kanji, from
Core 2000 cards). Every other kanji word is shown in hiragana.

Readings come from UniDic, which misreads some words (富士山 → ふじやま).
When the model also supplies a hiragana reading of the whole sentence, the two
are aligned and the model's reading wins wherever they disagree.
"""
import difflib
import re
from dataclasses import dataclass, field

from ..jp import _ruby_segments, has_kanji, kata_to_hira
from . import morph
from .profile import Profile

_NOISE = re.compile(r"[\s、。！？!?.,，・「」『』（）()〜～…ー\-]")


@dataclass
class Segment:
    text: str        # what the learner sees
    source: str      # the original token surface
    kanji: bool      # shown in kanji (learned spelling)
    reading: str     # hiragana reading
    pos: str = ""
    gap: str = ""    # text between the previous token and this one (kept as written)


@dataclass
class Rendered:
    display: str
    segments: list = field(default_factory=list)
    reading_fixes: list = field(default_factory=list)   # [(surface, unidic_kana, used_kana)]


def _norm(s: str) -> str:
    return kata_to_hira(_NOISE.sub("", s))


def _mapper(ours: str, theirs: str):
    """Map positions in `ours` to `theirs` where the alignment is unambiguous."""
    ops = difflib.SequenceMatcher(None, ours, theirs, autojunk=False).get_opcodes()

    def at(p):
        for tag, i1, i2, j1, j2 in ops:
            if tag == "equal" and i1 <= p <= i2:
                return j1 + (p - i1)
            if tag != "equal" and p in (i1, i2):
                return j1 if p == i1 else j2
        return None
    return at


def render(text: str, profile: Profile, reading: str | None = None, all_kanji: bool = False) -> Rendered:
    """all_kanji: keep every kanji (chat bubbles, where furigana gives the reading);
    otherwise kanji the learner hasn't learned become kana (plain-text places)."""
    toks = morph.tokenize(text)
    segs, spans, ours = [], [], ""
    for t in toks:
        known = (all_kanji or not has_kanji(t.surface)
                 or t.orth in profile.kanji or t.surface in profile.kanji)
        kana = t.kana if has_kanji(t.surface) else t.surface
        segs.append(Segment(t.surface if known else kana, t.surface, known and has_kanji(t.surface),
                            kana, t.pos1))
        n = _norm(kana)
        spans.append((len(ours), len(ours) + len(n)))
        ours += n

    fixes = []
    if reading:
        theirs = _norm(reading)
        if theirs != ours:
            at = _mapper(ours, theirs)
            for seg, (a, b) in zip(segs, spans):
                if not has_kanji(seg.source) or b == a:
                    continue
                ja, jb = at(a), at(b)
                if ja is None or jb is None or jb <= ja:
                    continue
                better = theirs[ja:jb]
                if better != _norm(seg.reading):
                    fixes.append((seg.source, seg.reading, better))
                    seg.reading = better
                    if not seg.kanji:
                        seg.text = better

    # Text between tokens (spaces the tokenizer skipped) is kept as written.
    out, pos = [], 0
    for t, seg in zip(toks, segs):
        seg.gap = text[pos:t.start]
        out += [seg.gap, seg.text]
        pos = t.end
    out.append(text[pos:])
    return Rendered("".join(out), segs, fixes)


_NON_WORDS = ("補助記号", "記号", "空白")


def kaiwa_tokens(r: Rendered) -> list:
    """The rendered sentence in jp.annotate()'s token format for the chat UI:
    furigana only over kanji the learner knows, tappable words by what's shown."""
    out = []
    for seg in r.segments:
        if seg.gap:
            out.append({"surface": seg.gap, "ruby": [{"t": seg.gap, "r": None}],
                        "reading": None, "pos": "空白", "word": False})
        ruby = (_ruby_segments(seg.text, seg.reading) if seg.kanji
                else [{"t": seg.text, "r": None}])
        out.append({"surface": seg.text, "ruby": ruby, "reading": seg.reading, "pos": seg.pos,
                    "word": seg.pos not in _NON_WORDS and bool(seg.text.strip())})
    return out
