"""Kana → kanji suggestions for the chat input, limited to kanji the learner knows.

For each word whose kanji the learner has learned (profile.kanji, from Core
2000), the table maps the kana they'd type to its kanji spelling: whole words
(えいが → 映画) and, for verbs and い-adjectives, conjugation stems, so that
たべます can become 食べます (たべ → 食べ), のみます → 飲みます, たかくない → 高くない.
Picking the right kanji is recall practice; unlearned kanji are never offered.
"""
from . import morph
from .profile import Profile

# godan verbs: dictionary ending → the kana their stems take (ない/ます/dict/ば/う + て/た forms)
GODAN = {"う": "わいうえおっ", "く": "かきくけこい", "ぐ": "がぎぐげごい", "す": "さしすせそ",
         "つ": "たちつてとっ", "ぬ": "なにぬねのん", "ぶ": "ばびぶべぼん", "む": "まみむめもん",
         "る": "らりるれろっ"}
# readings UniDic doesn't give as the dictionary form but learners type constantly
EXTRA_READINGS = {"何": ["なに"]}


def table(profile: Profile) -> dict:
    """{kana: [kanji spellings]}; the client matches the kana before the cursor."""
    out: dict = {}

    def add(kana: str, kanji: str):
        if kana and kanji != kana and kanji not in out.setdefault(kana, []):
            out[kana].append(kanji)

    for orth in sorted(profile.kanji):
        toks = morph.tokenize(orth)
        if len(toks) != 1:  # compounds the tokenizer splits: whole word only
            add("".join(t.kana for t in toks), orth)
            continue
        t = toks[0]
        reading = t.kana
        add(reading, orth)
        for r in EXTRA_READINGS.get(orth, []):
            add(r, orth)
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
    return out
