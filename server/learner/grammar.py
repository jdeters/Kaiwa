"""Grammar concepts, textbook mappings, and deterministic detection.

detect() walks UniDic tokens and reports which grammar constructions a
sentence uses. Each Hit lists the concept ids that would license it (any one
being known is enough — e.g. a bare に can be destination, time or target).
A Hit with no concept ids is a construction we don't model; the validator
rejects it so unknown grammar never slips through silently.
"""
import os
from dataclasses import dataclass, field

import yaml


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONTENT = os.path.join(ROOT, "content")


# ------------------------------------------------------------------ catalogs

def load_concepts() -> dict:
    with open(os.path.join(CONTENT, "grammar", "concepts.yaml"), encoding="utf-8") as f:
        return {c["id"]: c for c in yaml.safe_load(f)["concepts"]}


def load_textbook(textbook_id: str) -> dict:
    with open(os.path.join(CONTENT, "textbooks", f"{textbook_id}.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def concepts_through(textbook: dict, lesson: int) -> set:
    known = set()
    for n, ids in textbook["lessons"].items():
        if int(n) <= lesson:
            known.update(ids)
    return known


# ----------------------------------------------------------------- detection

@dataclass
class Hit:
    options: tuple          # concept ids that license this; () = unmodeled
    label: str              # human-readable, used in repair feedback
    i: int                  # token span [i, j)
    j: int
    function: list = field(default_factory=list)  # token indices that are grammar, not vocabulary


NI = ("particle.ni.destination", "particle.ni.time", "particle.ni.target")
DE = ("particle.de.location", "particle.de.means")
NO = ("no.affiliation", "no.possession", "no.modifier")

_PARTICLES = {
    # (pos2, surface) → concept options
    ("係助詞", "は"): ("copula.desu",),
    ("係助詞", "も"): ("particle.mo",),
    ("格助詞", "を"): ("particle.wo.object",),
    ("格助詞", "に"): NI,
    ("格助詞", "で"): DE,
    ("格助詞", "と"): ("particle.to.and", "particle.to.with"),
    ("格助詞", "から"): ("particle.kara.origin", "kara.made.time"),
    ("格助詞", "の"): NO,
    ("格助詞", "が"): ("particle.ga.subject",),
    ("副助詞", "まで"): ("kara.made.time",),
    ("副助詞", "か"): ("copula.desu.question",),   # なにか／どこか-style indefinites
    ("終助詞", "か"): ("copula.desu.question",),
    ("終助詞", "ね"): ("copula.desu",),
    ("終助詞", "よ"): ("copula.desu",),
    ("接続助詞", "が"): ("conj.ga.but",),
    ("接続助詞", "から"): ("conj.kara.because",),
    ("準体助詞", "の"): ("nominalizer.no",),
    ("副助詞", "や"): ("particle.ya",),
    ("格助詞", "や"): ("particle.ya",),
}


def _lemmas(toks, i, n):
    return [t.lemma for t in toks[i:i + n]]


def _verb_form(toks: list, i: int) -> Hit:
    """Classify the inflection that follows the verb at i."""
    v = toks[i]
    nxt = toks[i + 1] if i + 1 < len(toks) else None
    seq = _lemmas(toks, i + 1, 4)

    if seq[:4] == ["ます", "ず", "です", "た"]:
        return Hit(("verb.masendeshita",), "～ませんでした", i, i + 5, list(range(i + 1, i + 5)))
    if seq[:2] == ["ます", "ず"]:
        return Hit(("verb.masen",), "～ません", i, i + 3, [i + 1, i + 2])
    if seq[:2] == ["ます", "た"]:
        return Hit(("verb.mashita",), "～ました", i, i + 3, [i + 1, i + 2])
    if seq[:1] == ["ます"]:
        if nxt.cform.startswith("意志推量形"):
            return Hit(("verb.mashou",), "～ましょう", i, i + 2, [i + 1])
        return Hit(("verb.masu",), "～ます", i, i + 2, [i + 1])
    if seq[:1] == ["たい"]:
        return Hit(("verb.tai",), "～たい", i, i + 2, [i + 1])
    if seq[:1] == ["ない"] and nxt.pos1 == "助動詞":
        after = toks[i + 2] if i + 2 < len(toks) else None
        if after and after.pos2 == "接続助詞" and after.surface == "で":  # ～ないでください
            return Hit(("verb.nai",), "～ないで", i, i + 3, [i + 1, i + 2])
        return Hit(("verb.nai",), "ない-form", i, i + 2, [i + 1])
    if nxt and nxt.pos1 == "助詞" and nxt.pos2 == "接続助詞" and nxt.surface in ("て", "で"):
        return Hit(("verb.te",), "て-form", i, i + 2, [i + 1])
    if seq[:1] == ["た"]:
        return Hit(("casual.verb_past",), "plain past ～た", i, i + 2, [i + 1])
    if nxt and nxt.pos1 == "助動詞":
        return Hit((), f"verb + {nxt.surface} ({nxt.lemma})", i, i + 2, [i + 1])
    if v.cform.startswith(("終止形", "連体形")):
        return Hit(("verb.dictionary",), "dictionary form", i, i + 1)
    if v.cform.startswith("命令形"):
        return Hit((), f"imperative {v.surface}", i, i + 1)
    return Hit((), f"verb form {v.surface} ({v.cform})", i, i + 1)


def _copula_negative(toks: list, k: int) -> int:
    """If じゃ/では ありません starts at k, return its length, else 0."""
    t = toks[k]
    if t.lemma != "だ" or t.surface not in ("じゃ", "で"):
        return 0
    j = k + 1
    if j < len(toks) and toks[j].surface == "は":  # では
        j += 1
    if _lemmas(toks, j, 3) == ["有る", "ます", "ず"]:
        return j + 3 - k
    return 0


def detect(toks: list) -> list:
    hits, used = [], set()

    def add(h: Hit):
        hits.append(h)
        used.update(h.function)

    for i, t in enumerate(toks):
        if i in used:
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else None

        if t.pos1 == "動詞":
            if t.lemma == "下さる" and t.cform.startswith("命令形"):
                add(Hit(("request.kudasai",), "～ください", i, i + 1, [i]))
                continue
            # お + stem + します: お願いします is lesson-4 vocabulary; other
            # humble お～します forms are unmodeled
            if (i > 0 and toks[i - 1].pos1 == "接頭辞" and t.cform.startswith("連用形")
                    and nxt and nxt.lemma == "為る"):
                opts = ("request.onegaishimasu",) if t.lemma == "願う" else ()
                form = _verb_form(toks, i + 1)
                # お願いします is one learned phrase: 願い is grammar here, not the word 願う
                core = [i - 1, i, i + 1] if opts else [i - 1, i + 1]
                add(Hit(opts, f"お{t.surface}します", i - 1, form.j, core + form.function))
                continue
            if t.lemma in ("有る", "居る"):
                add(Hit(("existence.arimasu_imasu",), "あります／います", i, i + 1))
            add(_verb_form(toks, i))

        elif t.pos1 == "形容詞" and t.lemma != "無い":
            seq = _lemmas(toks, i + 1, 3)
            if t.cform.startswith("語幹"):  # 美味しそう, 高すぎる
                tail = nxt.surface if nxt else ""
                add(Hit((), f"adjective stem + {tail}", i, i + 2, [i + 1] if nxt else []))
            elif t.cform == "連用形-促音便" and seq[:1] == ["た"]:
                add(Hit(("adj.past",), "adjective past ～かった", i, i + 2, [i + 1]))
            elif seq[:1] == ["無い"]:
                func = [i + 1]
                if _lemmas(toks, i + 2, 1) == ["た"]:
                    add(Hit(("adj.past",), "～くなかった", i, i + 3, [i + 1, i + 2]))
                    continue
                add(Hit(("adj.i.negative",), "～くないです", i, i + 2, func))
            elif nxt and nxt.surface in ("て",) and nxt.pos1 == "助詞":
                add(Hit(("verb.te",), "adjective て-form", i, i + 2, [i + 1]))
            elif nxt and nxt.pos1 in ("名詞", "代名詞"):
                add(Hit(("adj.i.attributive",), "い-adjective + noun", i, i + 1))
            else:
                add(Hit(("adj.i.predicate",), "い-adjective predicate", i, i + 1))

        elif t.pos1 == "形状詞" and t.pos2 == "助動詞語幹":  # ～そう／～よう／～みたい
            add(Hit((), f"～{t.surface}", i, i + 1, [i]))

        elif t.pos1 == "形状詞" and nxt:
            n = _copula_negative(toks, i + 1)
            if n:
                add(Hit(("adj.na.negative",), "な-adjective じゃありません", i, i + 1 + n,
                        list(range(i + 1, i + 1 + n))))
            elif nxt.lemma == "だ" and nxt.surface == "な":
                add(Hit(("adj.na.attributive",), "な-adjective + な + noun", i, i + 2, [i + 1]))
            elif _lemmas(toks, i + 1, 2) == ["です", "た"]:
                add(Hit(("adj.past",), "な-adjective でした", i, i + 3, [i + 1, i + 2]))
            elif nxt.lemma == "です":
                add(Hit(("adj.na.predicate",), "な-adjective です", i, i + 2, [i + 1]))
            elif nxt.lemma == "だ":
                add(Hit(("casual.copula",), "plain だ", i, i + 2, [i + 1]))

        elif t.pos1 == "助動詞":
            n = _copula_negative(toks, i)
            if n:
                add(Hit(("copula.desu.negative",), "N じゃありません", i, i + n, list(range(i, i + n))))
            elif t.lemma == "です" and _lemmas(toks, i + 1, 1) == ["た"]:
                add(Hit(("copula.past",), "N でした", i, i + 2, [i, i + 1]))
            elif t.lemma == "です" and t.cform.startswith("意志推量形"):
                add(Hit((), "でしょう", i, i + 1, [i]))
            elif t.lemma == "です":
                add(Hit(("copula.desu",), "N です", i, i + 1, [i]))
            elif t.lemma == "だ":
                add(Hit(("casual.copula",), "plain だ", i, i + 1, [i]))
            else:
                add(Hit((), f"auxiliary {t.surface} ({t.lemma})", i, i + 1, [i]))

        elif t.pos1 == "助詞":
            opts = _PARTICLES.get((t.pos2, t.surface))
            if opts is None:
                add(Hit((), f"particle {t.surface}", i, i + 1, [i]))
            else:
                add(Hit(opts, f"particle {t.surface}", i, i + 1, [i]))
    return hits
