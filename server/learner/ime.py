"""Kana → kanji suggestions for the chat input, limited to kanji the learner knows.

For each word whose kanji the learner has learned (profile.kanji, from Core
2000), the table maps the kana they'd type to its kanji spelling: whole words
(えいが → 映画) and, for verbs and い-adjectives, conjugation stems, so that
たべます can become 食べます (たべ → 食べ), のみます → 飲みます, たかくない → 高くない.
Picking the right kanji is recall practice; unlearned kanji are never offered.
Kana words the learner knows only from the textbook (にほん) are offered in
their usual spelling from JMdict when every kanji in it is learned (日本).

Each kanji in those words is also offered on its own, by its readings from
KANJIDIC2 (らい → 来, so 来週 can be typed らい + しゅう even if 来週 itself isn't
a learned word). Only readings attested in a learned word (来年 → らい) or a
kanji's primary on-reading are used, so rare ones (たい for 来) don't add noise;
these come after whole-word suggestions. Shortened readings no dictionary lists
are worked out from the words (日本 にほん − 本 ほん → 日 に, a prefix of にち).
"""
import glob
import json
import os
import re
from functools import lru_cache

from .. import dictionary
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
    # kana-only words, spelled with learned kanji (にほん → 日本) — the
    # spelling of the same word, matched by meaning (ぜんぶ "all" is 全部, not
    # 前部 "front part"), and only where no learned word already covers the kana
    learned = {c for w in profile.kanji for c in w if _is_kanji(c)}
    spelled = {}
    dic = dictionary.spellings()
    for w in profile.snapshot["words"]:
        kana = w["reading"]
        if not w["lemma"].startswith("reading:") or w.get("part") or kana in out:
            continue
        meaning = _gloss_words(w.get("meaning", ""))
        options = [form for form, gloss in dic.get(kana, [])
                   if all(c in learned or c in _HIRAGANA for c in form) and any(_is_kanji(c) for c in form)
                   and (meaning & set(gloss) if meaning else True)]
        if len(options) > 1 and not meaning:   # can't tell which word it is
            continue
        for form in options:
            toks = morph.tokenize(form)
            if len(toks) == 1:
                add_word(form, kana, toks[0])
            else:
                add(kana, form)
            spelled[form] = kana
    _add_single_kanji(profile, out, spelled)
    return out


def _is_kanji(c: str) -> bool:
    return "\u4e00" <= c <= "\u9fff"


_HIRAGANA = set(chr(c) for c in range(0x3041, 0x3097))
_STOP = {"a", "an", "the", "to", "of", "in", "on", "at", "for", "be", "is", "it", "or", "and",
         "one", "s", "something", "someone", "thing", "things", "e", "g", "etc", "esp", "very"}


def _gloss_words(text: str) -> set:
    return set(re.findall(r"[a-z]+", text.lower())) - _STOP


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


def _add_single_kanji(profile: Profile, table: dict, spelled: dict = {}):
    """spelled: {kanji spelling: kana} of known kana words, which attest readings too."""
    dic = _kanjidic()
    if not dic:
        return
    readings = {}   # learned word → its reading, to attest kanji readings
    for orth in profile.kanji:
        readings[orth] = "".join(t.kana for t in morph.tokenize(orth))
    readings.update(profile.kanji_readings)
    readings.update(spelled)
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
    # shortened readings no dictionary lists, worked out from two-kanji words:
    # 日本 にほん minus 本 ほん leaves 日 に — kept if it starts a listed reading (にち)
    for w, wr in sorted(readings.items()):
        if len(w) != 2 or not all(c in dic for c in w):
            continue
        a, b = w
        guesses = [(a, wr[:-len(r)]) for r in sum(dic[b], []) if r and wr.endswith(r)]
        guesses += [(b, wr[len(r):]) for r in sum(dic[a], []) if r and wr.startswith(r)]
        for ch, r in guesses:
            if r and any(x.startswith(r) for x in sum(dic[ch], [])) and ch not in table.setdefault(r, []):
                table[r].append(ch)
