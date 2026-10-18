"""Kana → kanji suggestions for the chat input, limited to kanji the learner knows.

For each word whose kanji the learner has learned (profile.kanji, from Core
2000), the table maps the kana they'd type to its kanji spelling: whole words
(えいが → 映画) and, for verbs and い-adjectives, conjugation stems, so that
たべます can become 食べます (たべ → 食べ), のみます → 飲みます, たかくない → 高くない.
Picking the right kanji is recall practice; unlearned kanji are never offered.

Each kanji in those words is also offered on its own, by its readings from
KANJIDIC2 (らい → 来, so 来週 can be typed らい + しゅう even if 来週 itself isn't
a learned word). Only readings attested in a learned word (来年 → らい) or a
kanji's primary on-reading are used, so rare ones (たい for 来) don't add noise;
these come after whole-word suggestions.
"""
import glob
import json
import os
from functools import lru_cache

from ..jp import kata_to_hira
from . import morph
from .grammar import ROOT
from .profile import Profile

# godan verbs: dictionary ending → the kana their stems take (ない/ます/dict/ば/う + て/た forms)
GODAN = {"う": "わいうえおっ", "く": "かきくけこい", "ぐ": "がぎぐげごい", "す": "さしすせそ",
         "つ": "たちつてとっ", "ぬ": "なにぬねのん", "ぶ": "ばびぶべぼん", "む": "まみむめもん",
         "る": "らりるれろっ"}


def table(profile: Profile) -> dict:
    """{kana: [kanji spellings]}; the client matches the kana before the cursor."""
    out: dict = {}

    def add(kana: str, kanji: str):
        if kana and kanji != kana and kanji not in out.setdefault(kana, []):
            out[kana].append(kanji)

    def add_word(orth: str, reading: str, t):
        add(reading, orth)
        if t.pos1 == "動詞" and len(orth) > 1 and orth[-1] == reading[-1]:
            stem, rstem = orth[:-1], reading[:-1]
            if t.ctype.startswith(("上一段", "下一段")):      # 食べ(る), 見(る)
                add(rstem, stem)
            elif t.ctype.startswith("五段"):                  # 飲ま/飲み/飲む/飲め/飲も/飲ん
                for c in GODAN.get(orth[-1], ""):
                    add(rstem + c, stem + c)
            elif t.ctype.startswith("カ行変格"):              # 来ます / 来ない / 来る
                for r in ("き", "こ", "く"):
                    add(r, stem)
        elif t.pos1 == "形容詞" and orth.endswith("い") and reading.endswith("い"):
            for c in "いくかけ":                              # 高い / 高くない / 高かった / 高ければ
                add(reading[:-1] + c, orth[:-1] + c)

    for orth in sorted(profile.kanji):
        toks = morph.tokenize(orth)
        if len(toks) != 1:  # compounds the tokenizer splits: whole word only
            add("".join(t.kana for t in toks), orth)
        else:
            add_word(orth, toks[0].kana, toks[0])
    # the deck's own readings, where UniDic's differ (私 わたし, 家 うち, 土曜日 どようび, 四つ よっつ)
    for orth, reading in sorted(profile.kanji_readings.items()):
        toks = morph.tokenize(orth)
        if len(toks) == 1:
            add_word(orth, reading, toks[0])
        else:
            add(reading, orth)
    _add_single_kanji(profile, out)
    return out


@lru_cache(maxsize=1)
def _kanjidic() -> dict:
    """{kanji: ([on readings], [kun stems])} in hiragana, from models/kanjidic2-en-*.json."""
    files = sorted(glob.glob(os.path.join(ROOT, "models", "kanjidic2-en-*.json")))
    if not files:
        return {}
    with open(files[-1], encoding="utf-8") as f:
        chars = json.load(f)["characters"]
    out = {}
    for c in chars:
        on, kun = [], []
        for g in (c.get("readingMeaning") or {}).get("groups", []):
            for r in g["readings"]:
                if r["type"] == "ja_on":
                    on.append(kata_to_hira(r["value"]))
                elif r["type"] == "ja_kun":
                    kun.append(r["value"].split(".")[0].strip("-"))
        out[c["literal"]] = (on, kun)
    return out


def _add_single_kanji(profile: Profile, table: dict):
    dic = _kanjidic()
    if not dic:
        return
    readings = {}   # learned word → its reading, to attest kanji readings
    for orth in profile.kanji:
        readings[orth] = "".join(t.kana for t in morph.tokenize(orth))
    for ch in sorted({c for w in profile.kanji for c in w if c in dic}):
        on, kun = dic[ch]
        words = [(w, r) for w, r in readings.items() if ch in w]
        for i, r in enumerate(on + kun):
            primary = i == 0 and on
            # attested: the word starts or ends with this kanji, and its reading with this reading
            attested = any((w.startswith(ch) and wr.startswith(r)) or (w.endswith(ch) and wr.endswith(r))
                           for w, wr in words)
            if r and (attested or primary) and ch not in table.setdefault(r, []):
                table[r].append(ch)   # after any whole-word suggestions for the same kana
