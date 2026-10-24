"""Local JMdict dictionary: fast, accurate word lookups (no LLM needed).

Builds data/jmdict.db (SQLite) once from models/jmdict-eng-*.json.
"""
import glob
import json
import os
import re
import sqlite3
from functools import lru_cache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "jmdict.db")
SPELLINGS = os.path.join(ROOT, "data", "jmdict_spellings.json")


def _source_json() -> str | None:
    hits = sorted(glob.glob(os.path.join(ROOT, "models", "jmdict-eng-*.json")))
    return hits[-1] if hits else None


def available() -> bool:
    return os.path.exists(DB) or _source_json() is not None


def build_if_needed():
    if os.path.exists(DB) or not _source_json():
        return
    src = _source_json()
    print(f"[dictionary] building {DB} from {os.path.basename(src)} …")
    with open(src, encoding="utf-8") as f:
        data = json.load(f)
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        form TEXT NOT NULL,          -- kanji or kana writing
        reading TEXT,                -- primary kana reading
        gloss TEXT NOT NULL,         -- '; '-joined senses
        pos TEXT,
        common INTEGER DEFAULT 0
    )""")
    rows = []
    for w in data["words"]:
        kanji = [k["text"] for k in w.get("kanji", [])]
        kana = [k["text"] for k in w.get("kana", [])]
        common = any(k.get("common") for k in w.get("kanji", []) + w.get("kana", []))
        reading = kana[0] if kana else ""
        senses = []
        pos = ""
        for s in w.get("sense", [])[:3]:
            glosses = [g["text"] for g in s.get("gloss", [])[:4]]
            if glosses:
                senses.append(", ".join(glosses))
            if not pos and s.get("partOfSpeech"):
                pos = ",".join(s["partOfSpeech"][:2])
        if not senses:
            continue
        gloss = "; ".join(senses)
        for form in set(kanji + kana):
            rows.append((form, reading, gloss, pos, int(common)))
    con.executemany(
        "INSERT INTO entries (form, reading, gloss, pos, common) VALUES (?,?,?,?,?)", rows)
    con.execute("CREATE INDEX idx_form ON entries(form)")
    con.commit()
    con.close()
    print(f"[dictionary] done: {len(rows)} forms indexed")


@lru_cache(maxsize=1)
def spellings() -> dict:
    """{kana: [[kanji spelling, gloss words, common], …]} for every JMdict word not
    usually written in kana (にほん → 日本 "japan"), extracted once from the JMdict
    source into data/jmdict_spellings.json. Rare, outdated and irregular spellings
    (鋤焼き) are left out; "common" is per spelling (すき焼き yes, すき焼 no)."""
    if not os.path.exists(SPELLINGS):
        src = _source_json()
        if not src:
            return {}
        with open(src, encoding="utf-8") as f:
            words = json.load(f)["words"]
        out: dict = {}
        for w in words:
            forms = [(k["text"], bool(k.get("common"))) for k in w.get("kanji", [])
                     if not {"rK", "oK", "sK", "iK", "ik", "io"} & set(k.get("tags", []))]
            senses = w.get("sense", [])
            if not forms or (senses and "uk" in senses[0].get("misc", [])):   # usually kana: いつも, ください
                continue
            glosses = " ".join(g["text"] for s in senses[:3] for g in s.get("gloss", []))
            gloss_words = sorted(set(re.findall(r"[a-z]+", glosses.lower())))
            for k in w.get("kana", []):
                applies = k.get("appliesToKanji") or ["*"]
                for form, common in forms:
                    if "*" in applies or form in applies:
                        out.setdefault(k["text"], []).append([form, gloss_words, common and bool(k.get("common"))])
        with open(SPELLINGS, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
    with open(SPELLINGS, encoding="utf-8") as f:
        return json.load(f)


_STOP = {"a", "an", "the", "to", "of", "in", "on", "at", "for", "be", "is", "it", "or", "and",
         "one", "s", "something", "someone", "thing", "things", "e", "g", "etc", "esp", "very"}


def gloss_words(text: str) -> set:
    return set(re.findall(r"[a-z]+", text.lower())) - _STOP


def spellings_of(kana: str, meaning: str) -> list:
    """[[spelling, common]] of the word `kana` that means `meaning`: ぜんぶ "all,
    wholly" → 全部, not 前部 "front part". Only kanji + hiragana spellings; with no
    meaning to go by, only an unambiguous word."""
    options = [(form, gloss, common) for form, gloss, common in spellings().get(kana, [])
               if all("\u4e00" <= c <= "\u9fff" or "\u3041" <= c <= "\u3096" for c in form)]
    want = gloss_words(meaning)
    if want:
        return [[form, common] for form, gloss, common in options if want & set(gloss)]
    return [[f, c] for f, _, c in options] if len({tuple(g) for _, g, _ in options}) == 1 else []


_con = None


def _conn():
    global _con
    if _con is None:
        build_if_needed()
        if not os.path.exists(DB):
            return None
        _con = sqlite3.connect(DB, check_same_thread=False)
        _con.row_factory = sqlite3.Row
    return _con


def _is_japanese(q: str) -> bool:
    return any("぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" for ch in q)


def search(query: str, limit: int = 30) -> list:
    """Dictionary-tab search: Japanese → prefix match on forms, English → gloss match."""
    con = _conn()
    q = (query or "").strip()
    if con is None or not q:
        return []
    if _is_japanese(q):
        rows = con.execute(
            "SELECT * FROM entries WHERE form LIKE ? "
            "ORDER BY (form=?) DESC, common DESC, length(form) LIMIT ?",
            (q + "%", q, limit * 2)).fetchall()
    else:
        if len(q) < 2:
            return []
        lq = q.lower()
        # rank word-boundary hits first so "sushi" beats "T·sushi·ma cat"
        rows = con.execute(
            "SELECT * FROM entries WHERE gloss LIKE ? "
            "ORDER BY ((' ' || lower(gloss)) LIKE ('% ' || ? || '%')) DESC, "
            "common DESC, instr(lower(gloss), ?), length(gloss) LIMIT ?",
            ("%" + q + "%", lq, lq, limit * 2)).fetchall()
    out, seen = [], set()
    for r in rows:
        key = (r["form"], r["gloss"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"form": r["form"], "reading": r["reading"], "meaning": r["gloss"],
                    "pos": r["pos"], "common": bool(r["common"])})
        if len(out) >= limit:
            break
    return out


def lookup(word: str, reading: str | None = None) -> dict | None:
    """Exact-form lookup; prefers common entries.

    `reading` (hiragana) disambiguates homographs: 本 is ほん "book" or
    もと "origin" — without it you get whichever JMdict ranks first.
    """
    con = _conn()
    if con is None or not word:
        return None
    rows = con.execute(
        "SELECT * FROM entries WHERE form=? ORDER BY common DESC LIMIT 10", (word,)
    ).fetchall()
    if not rows:
        return None
    best = rows[0]
    if reading:
        best = next((r for r in rows if r["reading"] == reading), best)
    return {"meaning": best["gloss"], "reading": best["reading"],
            "pos": best["pos"], "common": bool(best["common"])}
