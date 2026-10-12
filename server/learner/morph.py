"""Morphological analysis for validation (fugashi + UniDic).

Validation runs on normal kanji-kana text: kana-only input with spaces is
ambiguous for the tokenizer (ほんが → 其れ), so generation asks for standard
orthography and the UI converts to kana afterwards.
"""
import re
from dataclasses import dataclass

from ..jp import kata_to_hira

_tagger = None


@dataclass
class Tok:
    surface: str
    pos1: str
    pos2: str
    lemma: str      # dictionary form, e.g. 行く (UniDic English glosses stripped)
    orth: str       # dictionary form as written, e.g. 食べる for 食べ (綺麗 vs lemma 奇麗)
    lform: str      # lemma reading in hiragana, e.g. いく
    kana: str       # surface reading in hiragana
    ctype: str
    cform: str
    start: int      # char offsets in the analyzed text
    end: int

    @property
    def content(self) -> bool:
        return self.pos1 in CONTENT_POS

    @property
    def key(self) -> str:
        """Word key for vocabulary lists: the lemma, except where UniDic gives a
        katakana lemma for a kanji word with several readings (金 → キン)."""
        if _KATAKANA.fullmatch(self.lemma) and any("一" <= c <= "鿿" for c in self.orth):
            return self.orth
        return self.lemma


_KATAKANA = re.compile(r"[ァ-ヺー]+")

CONTENT_POS = {"名詞", "動詞", "形容詞", "形状詞", "副詞", "代名詞", "連体詞",
               "感動詞", "接頭辞", "接尾辞", "接続詞"}


def _f(v) -> str:
    v = str(v or "")
    return "" if v == "*" else v


def tokenize(text: str) -> list[Tok]:
    global _tagger
    if _tagger is None:
        import fugashi
        _tagger = fugashi.Tagger()
    out, pos = [], 0
    for w in _tagger(text):
        f = w.feature
        start = text.find(w.surface, pos)
        if start < 0:
            start = pos
        end = start + len(w.surface)
        pos = end
        out.append(Tok(
            surface=w.surface,
            pos1=_f(f.pos1), pos2=_f(f.pos2),
            lemma=_f(f.lemma).split("-")[0] or w.surface,
            orth=_f(f.orthBase) or w.surface,
            lform=kata_to_hira(_f(f.lForm)),
            kana=kata_to_hira(_f(f.kana) or w.surface),
            ctype=_f(f.cType), cform=_f(f.cForm),
            start=start, end=end,
        ))
    return out
