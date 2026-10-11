"""The learner profile: known vocabulary, set phrases, and unlocked grammar.

build() imports from Anki + textbook progress and writes a JSON snapshot;
Profile loads a snapshot and answers "is this token known?" for the validator.
"""
import json
import os
import re
import time

import yaml

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
    kanji: dict = {}     # written form (orthBase) → source, for words whose kanji is known
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

    def add_reading(kana, meaning, tier, source, n):
        add_word(f"reading:{kana}", kana, meaning, tier, source, n)

    def add_entry(text, reading, meaning, tier, source, n, teaches_kanji=False):
        for variant in entry_variants(text):
            toks = morph.tokenize(variant)
            # Kanji spellings are learned only from sources that teach them
            # (Core 2000), and only once the card is past the learning stage.
            if teaches_kanji and TIER_RANK[tier] >= kanji_min:
                for t in toks:
                    if _has_kanji(t.orth):
                        kanji.setdefault(t.orth, source)
            content = [t for t in toks if t.content and t.pos2 != "数詞"]
            if _has_kanji(variant):
                for t in content:
                    add_word(t.lemma, t.lform, meaning if len(content) == 1 else "", tier, source, n)
            else:
                # Kana-only entries tokenize unreliably (ひるごはん → 蛭 + …), so
                # trust readings, not guessed lemmas, and match the entry whole.
                for t in content:
                    if len(t.lform) >= 2:
                        add_reading(t.lform, meaning if len(content) == 1 else "", tier, source, n)
                add_reading(_hira(variant), meaning, tier, source, n)
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

    return {
        "built_at": time.time(),
        "textbook": cfg["textbook"],
        "lesson": lesson,
        "grammar": sorted(grammar.concepts_through(textbook, lesson)),
        "words": sorted(words.values(), key=lambda w: w["lemma"]),
        "phrases": sorted(phrases.values(), key=lambda p: p["kana"]),
        "kanji_words": dict(sorted(kanji.items())),
        "stats": stats,
    }


def save(snapshot: dict, path: str = SNAPSHOT_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------- load

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
        with open(os.path.join(grammar.CONTENT, "closed_class.yaml"), encoding="utf-8") as f:
            self.closed = set(yaml.safe_load(f)["words"])

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
        if tok.lemma in self.lemmas:
            return True
        return bool(tok.lform) and tok.lform in self.readings

    def vocab_for_prompt(self) -> list[str]:
        """Known words for the generation prompt: "lemma(reading)", or bare kana
        for words only known by reading (kana-only textbook entries)."""
        out, covered = [], set()
        for w in self.lemmas.values():
            out.append(w["lemma"] if w["lemma"] == w["reading"] or not w["reading"]
                       else f"{w['lemma']}({w['reading']})")
            covered.add(w["reading"])
        for w in self.snapshot["words"]:
            if w["lemma"].startswith("reading:") and w["reading"] not in covered:
                out.append(w["reading"])
                covered.add(w["reading"])
        return out
