"""The learner profile: known vocabulary, set phrases, and unlocked grammar.

build() imports from Anki + textbook progress and writes a JSON snapshot;
Profile loads a snapshot and answers "is this token known?" for the validator.
"""
import json
import os
import re
import time

import yaml

from .. import dictionary
from ..jp import kata_to_hira
from . import anki, grammar, morph

ROOT = grammar.ROOT
DATA = os.path.join(ROOT, "data")
CONFIG_PATH = os.path.join(DATA, "learner.yaml")
EXAMPLE_CONFIG = os.path.join(grammar.CONTENT, "learner.example.yaml")
SNAPSHOT_PATH = os.path.join(DATA, "learner_profile.json")

TIER_RANK = {"textbook": 1, "learning": 1, "familiar": 2, "established": 3}

_PAREN = re.compile(r"[（(]\s*([^)）]*)\s*[)）]")
_PUNCT = re.compile(r"[\s。、！？!?.,・〜～「」…]")


def set_config_lesson(lesson: int, path: str = CONFIG_PATH):
    """Update `lesson:` in learner.yaml in place (keeps the file's comments)."""
    load_config(path)  # creates it from the example if missing
    with open(path, encoding="utf-8") as f:
        text = f.read()
    text, n = re.subn(r"(?m)^lesson:\s*\d+", f"lesson: {int(lesson)}", text)
    if not n:
        text += f"\nlesson: {int(lesson)}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def load_config(path: str = CONFIG_PATH) -> dict:
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(EXAMPLE_CONFIG, encoding="utf-8") as src, open(path, "w", encoding="utf-8") as dst:
            dst.write(src.read())
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def entry_variants(text: str) -> list[str]:
    """Deck entries mark optional parts in parentheses: "(お) べんとう" →
    おべんとう / べんとう; "きれい (な)" → きれいな / きれい."""
    out = []
    for part in re.split(r"[/／]", text):
        for v in (_PAREN.sub("", part), _PAREN.sub(r"\1", part)):
            v = _PUNCT.sub("", v)
            if v and v not in out:
                out.append(v)
    return out


_KATAKANA = re.compile(r"[ァ-ヺー]+")


def spaced_words(text: str) -> set:
    """Readings of the words a kana entry spells out between spaces/punctuation:
    a chunk counts when it's one word plus only endings or particles (いかが ですか,
    ありがとう ございます, おおさか に). The textbook writes kana with spaces between
    words, so this is a reliable boundary; a chunk the tokenizer splits into two
    words (よやく → よ + やく) or that starts with a particle (かいしゃ → か + いしゃ)
    gives nothing."""
    out = set()
    for chunk in re.split(r"[\s、。！？!?・/／()（）〜～.…]+", text):
        toks = morph.tokenize(chunk) if chunk else []
        while toks and toks[0].pos1 == "接頭辞":      # お/ご: おてあらい, おじゃまします
            toks = toks[1:]
        if not toks or not toks[0].content:
            continue
        tail = toks[1:]
        if all(t.pos1 in ("助詞", "助動詞") or t.pos2 == "非自立可能" for t in tail):
            out.add(toks[0].lform)
        # loanword compounds split reliably: オレンジジュース → オレンジ, ジュース
        out |= {t.lform for t in toks if t.content and _KATAKANA.fullmatch(t.surface)}
    return out


def _min_lesson(a, b):
    return b if a is None else a if b is None else min(a, b)


def _has_kanji(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in s)


def _hira(s: str) -> str:
    return kata_to_hira(_PUNCT.sub("", s))


# -------------------------------------------------------------------- build

def build(cfg: dict) -> dict:
    url = cfg.get("anki_url", anki.DEFAULT_URL)
    textbook = grammar.load_textbook(cfg["textbook"])
    lesson = int(cfg["lesson"])
    words: dict = {}     # lemma → record
    whole_kana: set = set()   # readings of whole kana-only entries
    pieces: list = []         # readings of pieces of kana-only entries, pending corroboration
    kanji: dict = {}     # written form (orthBase) → source, for words whose kanji is known
    kanji_readings: dict = {}   # learned kanji entry → the deck's reading (私 → わたし, not UniDic's わたくし)
    kanji_min = TIER_RANK[cfg.get("kanji_min_tier", "familiar")]
    phrases: dict = {}   # hiragana → record
    stats = {}

    def add_word(lemma, reading, meaning, tier, source, n):
        rec = words.get(lemma)
        if rec is None or TIER_RANK[tier] > TIER_RANK[rec["tier"]]:
            words[lemma] = {"lemma": lemma, "reading": reading, "meaning": meaning,
                            "tier": tier, "source": source,
                            "lesson": n if rec is None else _min_lesson(rec["lesson"], n)}
        else:
            rec["lesson"] = _min_lesson(rec["lesson"], n)

    def add_reading(kana, meaning, tier, source, n, part=False):
        """part: not a word in its own right — a fragment of a kana-only entry
        (ひるごはん → ひる) or a whole multi-word entry (わたしはスミスです).
        Both still match vocabulary; neither is tracked as a word."""
        key = f"reading:{kana}"
        new = key not in words
        add_word(key, kana, meaning, tier, source, n)
        if not part:
            words[key].pop("part", None)
        elif new:
            words[key]["part"] = True

    def add_entry(text, reading, meaning, tier, source, n, teaches_kanji=False):
        for variant in entry_variants(text):
            toks = morph.tokenize(variant)
            # Kanji spellings are learned only from sources that teach them
            # (Core 2000), and only once the card is past the learning stage.
            if teaches_kanji and TIER_RANK[tier] >= kanji_min:
                for t in toks:
                    if _has_kanji(t.orth):
                        kanji.setdefault(t.orth, source)
                if reading and _has_kanji(variant):
                    kanji_readings.setdefault(variant, _hira(reading))
            content = [t for t in toks if t.content and t.pos2 != "数詞"]
            if _has_kanji(variant) and len(toks) > 1 and (
                    len(content) > 1 or any(t.pos2 == "数詞" for t in toks)):
                # A compound the tokenizer splits (土曜日 → 土曜 + 日, 一つ → 一 + つ)
                # is one word, tracked whole; its pieces only license the tokens.
                add_word(variant, _hira(reading) or _hira("".join(t.kana for t in toks)),
                         meaning, tier, source, n)
                words[variant]["parts"] = [t.key for t in toks]
                for t in content:
                    new = t.key not in words
                    add_word(t.key, t.lform, "", tier, source, n)
                    if new:
                        words[t.key]["part"] = True
            elif _has_kanji(variant):
                for t in content:
                    add_word(t.key, t.lform, meaning, tier, source, n)
                    words[t.key].pop("part", None)   # a word in its own right too
            else:
                # Kana-only entries tokenize unreliably (かいしゃ → か + いしゃ "doctor",
                # よやく → よ + やく), so trust readings, not guessed lemmas, and match
                # the entry whole. Its pieces wait for corroboration (below).
                add_reading(_hira(variant), meaning, tier, source, n, part=len(content) > 1)
                whole_kana.add(_hira(variant))
                for t in content:
                    if len(t.lform) >= 2:
                        pieces.append((t.lform, meaning if len(content) == 1 else "", tier, source, n,
                                       len(content) > 1, t.lform in spaced_words(text)))
            # Every entry is also licensed whole by its kana: covers multi-word
            # entries (まってください) and compounds the tokenizer splits
            # differently in kanji (ふじさん vs 富士 + 山).
            if len(toks) > 1 or not _has_kanji(variant):
                kana = _hira("".join(t.kana for t in toks))
                phrases.setdefault(kana, {"text": variant, "kana": kana, "meaning": meaning,
                                          "source": source, "lesson": n})
        if reading:  # the deck's own reading for the whole word (一つ → ひとつ)
            add_reading(_hira(reading), meaning, tier, source, n)

    for src in cfg["sources"]:
        cards = anki.deck_cards(src["deck"], url)
        by_note: dict = {}
        for c in cards:
            by_note.setdefault(c["note"], []).append(c)
        n_used = 0
        for note_cards in by_note.values():
            fields = note_cards[0]["fields"]
            tiers = [anki.card_tier(c) for c in note_cards]
            best = max((t for t in tiers if t != "new"), key=TIER_RANK.get, default=None)
            n = None  # lesson number, for textbook sources
            if src["include"] == "studied":
                if best is None:
                    continue
                tier = best
            elif src["include"] == "lessons":
                n = anki.lesson_of(note_cards[0]["deckName"], src["lesson_pattern"])
                if n is None or n > lesson:
                    continue
                tier = best or "textbook"
            else:
                raise ValueError(f"unknown include mode: {src['include']}")
            text = anki.clean_field(fields[src["word_field"]]["value"])
            if not text:
                continue
            reading = anki.clean_field(fields[src["reading_field"]]["value"]) if src.get("reading_field") else ""
            meaning = anki.clean_field(fields[src["meaning_field"]]["value"]) if src.get("meaning_field") else ""
            add_entry(text, reading, meaning, tier, src["deck"], n, src.get("kanji", False))
            n_used += 1
        stats[src["deck"]] = n_used

    # A piece of a kana-only entry counts only if something backs it up: it's a
    # whole entry itself (ひる from ひるごはん), the reading of a word learned
    # with its kanji (いく for 行く), or the textbook's own spacing marks it as a
    # word (spaced_words). Misparse leftovers (いしゃ, やく) are dropped.
    lemma_readings = {w["reading"] for k, w in words.items() if not k.startswith("reading:")}
    for kana, meaning, tier, source, n, part, verb_form in pieces:
        if verb_form or kana in whole_kana or kana in lemma_readings:
            add_reading(kana, meaning, tier, source, n, part=part)

    return {
        "built_at": time.time(),
        "textbook": cfg["textbook"],
        "lesson": lesson,
        "grammar": sorted(grammar.concepts_through(textbook, lesson)),
        "words": sorted(words.values(), key=lambda w: w["lemma"]),
        "phrases": sorted(phrases.values(), key=lambda p: p["kana"]),
        "kanji_words": dict(sorted(kanji.items())),
        "kanji_readings": dict(sorted(kanji_readings.items())),
        "kana_spellings": kana_spellings(words.values(), kanji_readings, kanji),
        "stats": stats,
    }


def kana_spellings(words, kanji_readings: dict, kanji_words=()) -> dict:
    """Words known only in kana, in their kanji spellings: {kana: [[form, common]]}
    (あした → 明日, なんようび → 何曜日). The partner writes them that way, and
    UniDic reads 明日 あす. Words learned with their kanji are left alone: みる is
    見る, and matching by meaning would also bring in 診る and 観る. Spellings JMdict
    doesn't mark common count only with learned kanji (何曜日, not 殺る for やる)."""
    words = list(words)
    learned = {c for w in kanji_words for c in w}
    with_kanji = ({w["reading"] for w in words if not w["lemma"].startswith("reading:")}
                  | set(kanji_readings.values()))
    out = {}
    for w in words:
        kana = w["reading"]
        if w["lemma"].startswith("reading:") and not w.get("part") and kana not in with_kanji:
            forms, seen = [], set()
            for form, common in dictionary.spellings_of(kana, w.get("meaning", "")):
                if form not in seen and (common or all(c in learned for c in form if _has_kanji(c))):
                    forms.append([form, common])
                    seen.add(form)
            if any(c for _, c in forms):   # 来週 is the word; 来秋 only shares its sound
                forms = [f for f in forms if f[1]]
            if forms:
                out[kana] = forms
    return dict(sorted(out.items()))


def save(snapshot: dict, path: str = SNAPSHOT_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------- load

_NUMERALS = set("〇一二三四五六七八九十百千万億")


def _numeral(s: str) -> bool:
    """百, 千, 二十: numbers, which are always allowed and never tracked as words."""
    return bool(s) and all(c in _NUMERALS for c in s)


class Profile:
    def __init__(self, snapshot: dict):
        self.snapshot = snapshot
        self.grammar = set(snapshot["grammar"])
        self.lemmas = {w["lemma"]: w for w in snapshot["words"] if not w["lemma"].startswith("reading:")}
        self.readings = {}
        for w in snapshot["words"]:
            if w["reading"]:
                self.readings.setdefault(w["reading"], w)
        self.phrases = [p["kana"] for p in snapshot["phrases"] if len(p["kana"]) >= 2]
        self.kanji = set(snapshot.get("kanji_words", {}))  # written forms shown in kanji
        self.kanji_readings = snapshot.get("kanji_readings", {})   # 私 → わたし, as the deck reads it
        # kana-only words in kanji (明日 → あした): one token is a known lemma;
        # more (何曜 + 日) are a compound, below
        self.kana_spellings = snapshot.get("kana_spellings", {})
        self.spelled = {}
        compounds = [(tuple(w["parts"]), w["lemma"]) for w in snapshot["words"] if w.get("parts")]
        for kana, forms in self.kana_spellings.items():
            for form, _ in forms:
                toks = morph.tokenize(form)
                if len(toks) == 1:
                    self.spelled.setdefault(toks[0].key, f"reading:{kana}")
                else:
                    compounds.append((tuple(t.key for t in toks), f"reading:{kana}"))
        # compounds the tokenizer splits, by their tokens' keys, longest first
        self.compounds = sorted(compounds, key=lambda c: -len(c[0]))
        with open(os.path.join(grammar.CONTENT, "closed_class.yaml"), encoding="utf-8") as f:
            self.closed = set(yaml.safe_load(f)["words"])
        # one id per word for tracking: a lemma (飲む), or "reading:…" for words
        # known only in kana; a kana reading of a known lemma maps to the lemma —
        # the deck's reading first (うち → 家, though UniDic reads 家 いえ)
        self.word_ids = {}
        # card readings of words that aren't tracked (時 とき is closed-class, 百
        # ひゃく a number): their kana entries mustn't become practice targets
        self._untracked_readings = set()
        for orth, reading in self.kanji_readings.items():
            if orth in self.lemmas and not _numeral(orth):
                self.word_ids.setdefault(reading, orth)
            elif orth in self.closed or _numeral(orth):
                self._untracked_readings.add(reading)
        for w in self.lemmas.values():
            if w["reading"]:
                self.word_ids.setdefault(w["reading"], w["lemma"])
        for w in snapshot["words"]:
            if w["lemma"].startswith("reading:"):
                self.word_ids.setdefault(w["reading"], w["lemma"])

    @classmethod
    def load(cls, path: str = SNAPSHOT_PATH) -> "Profile":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def restricted(self, lesson: int | None = None, words_from: set | None = None) -> "Profile":
        """A copy limited to an earlier lesson and/or some sources (for eval profiles)."""
        snap = dict(self.snapshot)
        if lesson is not None:
            textbook = grammar.load_textbook(snap["textbook"])
            snap["lesson"] = lesson
            snap["grammar"] = sorted(grammar.concepts_through(textbook, lesson))
            snap["words"] = [w for w in snap["words"] if (w.get("lesson") or 0) <= lesson]
            snap["phrases"] = [p for p in snap["phrases"] if (p.get("lesson") or 0) <= lesson]
        if words_from is not None:
            snap["words"] = [w for w in snap["words"] if w["source"] in words_from]
            snap["phrases"] = [p for p in snap["phrases"] if p["source"] in words_from]
            snap["kanji_words"] = {k: v for k, v in snap.get("kanji_words", {}).items()
                                   if v in words_from}
        return Profile(snap)

    def known(self, tok: morph.Tok) -> bool:
        if tok.pos2 == "数詞":
            return True
        if tok.lemma in self.closed or tok.surface in self.closed:
            return True
        if tok.key in self.lemmas or tok.lemma in self.lemmas or tok.key in self.spelled:
            return True
        return bool(tok.lform) and tok.lform in self.readings

    def word_id(self, tok: morph.Tok) -> str | None:
        """Tracking id of a known content word, or None (unknown words,
        closed-class words, names, numbers, prefixes/suffixes)."""
        if not tok.content or tok.pos1 in ("接頭辞", "接尾辞") or tok.pos2 in ("数詞", "固有名詞"):
            return None
        if tok.lemma in self.closed or tok.surface in self.closed:
            return None
        if tok.key in self.lemmas:
            return tok.key
        if tok.lemma in self.lemmas:   # snapshots built before Tok.key
            return tok.lemma
        if tok.key in self.spelled:    # 明日 → reading:あした
            return self.word_ids.get(self.spelled[tok.key][len("reading:"):], self.spelled[tok.key])
        return self.word_ids.get(tok.lform)

    def word_spans(self, toks: list) -> list[tuple[int, int, str]]:
        """(start, end, word id) for each tracked word in toks; a compound entry
        the tokenizer splits (土曜日 → 土曜 + 日) is one word."""
        out, i = [], 0
        while i < len(toks):
            for parts, lemma in self.compounds:
                if tuple(t.key for t in toks[i:i + len(parts)]) == parts:
                    out.append((i, i + len(parts), lemma))
                    i += len(parts)
                    break
            else:
                wid = self.word_id(toks[i])
                if wid:
                    out.append((i, i + 1, wid))
                i += 1
        return out

    def word_list(self) -> list[str]:
        """Every tracked word once, lemmas first (Core 2000, by tier), then
        kana-only textbook words; matching fragments are left out."""
        rank = {"established": 0, "familiar": 1, "learning": 2, "textbook": 3}
        ids = sorted({i for i in self.word_ids.values()},
                     key=lambda i: (i.startswith("reading:"),
                                    rank.get((self.lemmas.get(i) or {}).get("tier"), 4), i))
        words = {w["lemma"]: w for w in self.snapshot["words"]}
        return [i for i in ids if not words.get(i, {}).get("part") and i not in self.closed
                and words.get(i, {}).get("reading", "x") not in self.closed
                and words.get(i, {}).get("reading", "x") not in self._untracked_readings
                and not _numeral(i)]

    def vocab_for_prompt(self) -> list[str]:
        """Known words for the generation prompt: "lemma(reading)", or bare kana
        for words only known by reading (kana-only textbook entries)."""
        out, covered = [], set()
        for w in self.lemmas.values():
            if w.get("part"):   # 土曜 of 土曜日: the compound is listed
                continue
            out.append(w["lemma"] if w["lemma"] == w["reading"] or not w["reading"]
                       else f"{w['lemma']}({w['reading']})")
            covered.add(w["reading"])
        for w in self.snapshot["words"]:
            if w["lemma"].startswith("reading:") and w["reading"] not in covered:
                out.append(w["reading"])
                covered.add(w["reading"])
        return out
