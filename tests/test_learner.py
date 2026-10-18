"""Validator regression tests against a small synthetic Lesson-10 profile.

Run: .venv/bin/python -m pytest tests/
"""
import pytest

from server.learner import display, grammar, ime, morph, validator
from server.learner.profile import Profile, entry_variants


def _word(lemma, reading, source="test", lesson=None):
    return {"lemma": lemma, "reading": reading, "meaning": "", "tier": "familiar",
            "source": source, "lesson": lesson}


def _phrase(kana, lesson=None):
    return {"text": kana, "kana": kana, "meaning": "", "source": "test", "lesson": lesson}


@pytest.fixture(scope="module")
def profile():
    textbook = grammar.load_textbook("jfbp-r4-kana")
    words = [_word(*w) for w in [
        ("飲む", "のむ"), ("食べる", "たべる"), ("行く", "いく"), ("見る", "みる"),
        ("映画", "えいが"), ("コーヒー", "こーひー"), ("友達", "ともだち"), ("昨日", "きのう"),
        ("寒い", "さむい"), ("今日", "きょう"), ("便利", "べんり"), ("地下鉄", "ちかてつ"),
        ("家", "いえ"), ("日曜", "にちよう"), ("良い", "よい"), ("本", "ほん"),
        ("面白い", "おもしろい"), ("願う", "ねがう"), ("待つ", "まつ"), ("ちょっと", "ちょっと"),
    ]] + [_word("reading:ふじさん", "ふじさん"), _word("reading:いま", "いま")]
    snap = {
        "textbook": "jfbp-r4-kana", "lesson": 10,
        "grammar": sorted(grammar.concepts_through(textbook, 10)),
        "words": words,
        "phrases": [_phrase("まってください"), _phrase("でした"), _phrase("いま"), _phrase("ふじさん"),
                    _phrase("ちかてつ")],
        # kanji spellings learned from Core 2000; everything else displays in kana
        "kanji_words": {"映画": "Core 2000", "見る": "Core 2000", "飲む": "Core 2000"},
    }
    return Profile(snap)


PASS = [
    "コーヒーを飲みますか。",
    "コーヒーをお願いします。",
    "昨日、友達と映画を見ました。",
    "今日は寒くないです。",
    "東京の地下鉄は便利です。",
    "この本は面白いです。",
    "ちょっと待ってください。",        # て-form licensed by a whole deck phrase
    "富士山はいいですね。",            # 富士+山 is a name; 山 misread by unidic-lite
]

FAIL = [
    ("昨日は寒かったです。", "adjective past"),
    ("いい映画でしたか。", "N でした"),            # grammar-only deck card must not license
    ("日曜日も家にいますか。", "あります／います"),  # いま must not match inside います
    ("映画を見たいです。", "～たい"),
    ("映画を見ましょう。", "～ましょう"),
    ("映画を見るのが好きです。", "dictionary form"),
    ("コーヒーを飲んだ。", "plain past"),
    ("本を見ています。", "て-form"),
    ("このコーヒーは美味しそうですね。", "adjective stem"),
    ("映画を見られます。", "verb + られ"),
    ("映画を見せてください。", "見せ"),                # unknown word
]


@pytest.mark.parametrize("text", PASS)
def test_passes(profile, text):
    r = validator.validate(text, profile)
    assert r.ok, [str(v) for v in r.violations]


@pytest.mark.parametrize("text,expect", FAIL)
def test_fails(profile, text, expect):
    r = validator.validate(text, profile)
    assert not r.ok
    assert any(expect in str(v) for v in r.violations), [str(v) for v in r.violations]


def test_concept_examples_detect_themselves():
    for cid, c in grammar.load_concepts().items():
        if c.get("enforced_by") == "vocabulary":
            continue
        hits = grammar.detect(morph.tokenize(c["example"]))
        assert any(cid in h.options for h in hits), (cid, c["example"])


def test_entry_variants():
    assert entry_variants("(お) べんとう") == ["べんとう", "おべんとう"]
    assert entry_variants("きれい (な)") == ["きれい", "きれいな"]
    assert entry_variants("よん/し") == ["よん", "し"]


@pytest.mark.parametrize("text,shown", [
    ("昨日、友達と映画を見ました。", "きのう、ともだちと映画を見ました。"),
    ("コーヒーを飲みますか。", "コーヒーを飲みますか。"),
    ("今日は寒くないです。", "きょうはさむくないです。"),
    ("銀行は9時から3時までです。", "ぎんこうは9じから3じまでです。"),
    ("東京の地下鉄は便利です。", "とうきょうのちかてつはべんりです。"),
])
def test_display_shows_only_known_kanji(profile, text, shown):
    assert display.render(text, profile).display == shown


def test_display_prefers_model_reading(profile):
    # unidic-lite reads 富士山 as ふじやま; the model's reading corrects it
    r = display.render("富士山に行きました。", profile, reading="ふじさんにいきました。")
    assert r.display == "ふじさんにいきました。"
    assert ("山", "やま", "さん") in r.reading_fixes


def test_display_ignores_unalignable_reading(profile):
    # a reading that doesn't match the sentence leaves UniDic's readings alone
    r = display.render("映画を見ました。", profile, reading="まったくちがうぶん")
    assert r.display == "映画を見ました。"


def test_ime_offers_only_learned_kanji(profile):
    # fixture kanji: 映画, 見る, 飲む — 食べる is a known word but not a known kanji spelling
    t = ime.table(profile)
    assert t["えいが"] == ["映画"]
    assert t["のみ"] == ["飲み"] and t["のま"] == ["飲ま"] and t["のむ"] == ["飲む"]
    assert t["み"] == ["見"]
    assert "たべ" not in t and "たべる" not in t


def test_ime_conjugation_stems():
    snap = {"textbook": "jfbp-r4-kana", "lesson": 10, "grammar": [], "words": [], "phrases": [],
            "kanji_words": {"食べる": "Core 2000", "高い": "Core 2000", "来る": "Core 2000",
                            "何": "Core 2000", "私": "Core 2000"},
            # the deck's readings, where UniDic's differ (何 なん, 私 わたくし)
            "kanji_readings": {"何": "なに", "私": "わたし"}}
    t = ime.table(Profile(snap))
    assert t["たべ"] == ["食べ"]
    assert t["たかく"] == ["高く"] and t["たかか"] == ["高か"]    # 高くない / 高かった
    assert t["き"] == ["来"] and t["こ"] == ["来"]
    assert "何" in t["なに"] and "何" in t["なん"]
    assert t["わたし"] == ["私"]     # the whole word, not 私 for the し


# ------------------------------------------------------------- production tracking

def _obs(profile, text, **kw):
    from server.learner import production
    return {(o.kind, o.item): o.outcome for o in production.observe(text, profile, **kw)}


def test_production_independent_use(profile):
    o = _obs(profile, "昨日、友達と映画を見ました。")
    assert o[("word", "見る")] == "independent" and o[("word", "映画")] == "independent"
    assert o[("grammar", "verb.mashita")] == "independent"
    assert o[("grammar", "particle.wo.object")] == "independent"
    assert ("grammar", "particle.ni.destination") not in o      # ambiguous particles skipped


def test_production_echo_hint_error(profile):
    o = _obs(profile, "コーヒーを飲みます。", prev_partner="何を飲みますか。")
    assert o[("word", "飲む")] == "echo"                         # partner just said it
    o = _obs(profile, "コーヒーを飲みます。", hint=True)
    assert set(o.values()) == {"hint"}
    o = _obs(profile, "コーヒーが飲みました。",
             errors=[{"wrong": "コーヒーが", "right": "コーヒーを"}])
    assert o[("word", "飲む")] == "independent"                  # the particle was wrong, not 飲む
    assert o[("grammar", "verb.mashita")] == "independent"


def test_production_flags_wrong_form(profile):
    o = _obs(profile, "昨日、映画を見ます。", errors=[{"wrong": "みます", "right": "みました"}])
    assert o[("grammar", "verb.masu")] == "error"


def test_word_key_uses_kanji_spelling_for_katakana_lemmas():
    assert [t.key for t in morph.tokenize("金")] == ["金"]       # UniDic lemma is キン
    assert [t.key for t in morph.tokenize("テレビ")] == ["テレビ"]  # real katakana words stay


# ------------------------------------------------------------- practice scheduling

def test_practice_intervals():
    from server.learner import practice
    D = practice.DAY
    st = practice.replay([("word", "飲む", "independent", 0),
                          ("word", "飲む", "independent", 60),          # same sitting: once
                          ("word", "飲む", "echo", 2 * D),              # exposure: no change
                          ("word", "飲む", "independent", 2 * D)])[("word", "飲む")]
    assert st.successes == 2 and st.interval == practice.FIRST_INTERVAL * practice.GROWTH
    assert st.due == 2 * D + st.interval * D
    st = practice.replay([("grammar", "verb.mashita", "independent", 0),
                          ("grammar", "verb.mashita", "independent", 2 * D),
                          ("grammar", "verb.mashita", "error", 3 * D)])[("grammar", "verb.mashita")]
    assert st.interval == 2.5 * practice.ERROR_FACTOR      # mistake: 2.5 days → 1 day
    assert st.due == 4 * D                                  # back the next day


def test_practice_pick_priority():
    import random
    from server.learner import practice
    D = practice.DAY
    events = [("word", "a", "independent", 0),                  # due long ago
              ("word", "b", "independent", 10 * D),             # not due yet
              ("word", "c", "error", 9 * D)]                    # mistake, never right
    cands = [("word", x) for x in "abcde"]
    picked = practice.pick(cands, practice.replay(events), now=10.5 * D, n=4, rng=random.Random(1))
    reasons = dict(picked)
    assert picked[0] == (("word", "c"), "recent mistake")
    assert reasons[("word", "a")] == "due"
    assert ("word", "b") not in reasons                         # not due: skipped
    assert {k for k, r in picked if r == "never said"} <= {("word", "d"), ("word", "e")}


def test_practice_report():
    from server.learner import practice
    targets = [{"kind": "word", "item": "飲む"}, {"kind": "grammar", "item": "verb.mashita"},
               {"kind": "word", "item": "見る"}, {"kind": "word", "item": "行く"}]
    obs = [("word", "飲む", "independent"), ("grammar", "verb.mashita", "error"),
           ("word", "見る", "echo")]
    assert [r["result"] for r in practice.report(targets, obs)] == ["hit", "tried", "assisted", "missed"]


def test_salvage_keeps_only_valid_sentences(profile):
    from server.learner import generate
    p = generate.Partner.__new__(generate.Partner)   # no API client needed for _salvage
    p.profile, p.concepts, p.avoid = profile, grammar.load_concepts(), set()
    drafts = [{"japanese": "そうですか。昨日は寒かったですか。"},   # adjective past: not learned
              {"japanese": "昨日、映画を見ましたか。"}]
    assert p._salvage(drafts) == "そうですか。昨日、映画を見ましたか。"
    assert p._salvage([{"japanese": "寒かったです。"}]) == "そうですか。"


def test_partner_may_not_say_unproduced_word_targets(profile):
    from server.learner import generate
    p = generate.Partner.__new__(generate.Partner)
    p.profile, p.concepts, p.avoid = profile, grammar.load_concepts(), {"映画"}
    assert p._target_violations("映画を見ましたか。")             # says the target: rejected
    assert not p._target_violations("昨日、何を見ましたか。")       # asks for it: fine
    assert p._salvage([{"japanese": "そうですか。映画は好きですか。"},
                       {"japanese": "昨日、何を見ましたか。"}]) == "そうですか。昨日、何を見ましたか。"


def test_onegaishimasu_is_grammar_not_the_word_negau(profile):
    o = _obs(profile, "コーヒーをお願いします。")
    assert o[("grammar", "request.onegaishimasu")] == "independent"
    assert ("word", "願う") not in o


def test_particles_resolved_by_labeled_use(profile):
    o = _obs(profile, "友達と家で映画を見ました。",
             particles=[{"particle": "と", "use": "to.with"}, {"particle": "で", "use": "de.location"}])
    assert o[("grammar", "particle.to.with")] == "independent"
    assert o[("grammar", "particle.de.location")] == "independent"
    o = _obs(profile, "友達と家で映画を見ました。")        # unlabeled: still not guessed
    assert ("grammar", "particle.de.location") not in o


def test_possibly_misheard_words_are_left_out(profile):
    # the corrector flagged a garbled word whose best guess is えいが: it isn't counted
    o = _obs(profile, "昨日、映画を見ました。", uncertain=["えいが"])
    assert ("word", "映画") not in o and ("word", "見る") in o


def test_grammar_inside_a_misheard_stretch_is_left_out(profile):
    o = _obs(profile, "九時に行きました。", uncertain=["九時に行きました"],   # as in the sentence
             particles=[{"particle": "に", "use": "ni.time"}])
    assert ("grammar", "particle.ni.time") not in o and ("grammar", "verb.mashita") not in o


def test_misheard_exclusion_is_positional(profile):
    # only the guessed stretch is left out; the に in 家に was heard fine
    o = _obs(profile, "家に帰りました。映画に行きました。", uncertain=["えいがに"],
             particles=[{"particle": "に", "use": "ni.destination"}, {"particle": "に", "use": "ni.destination"}])
    assert o.get(("grammar", "particle.ni.destination")) == "independent"
    assert ("word", "映画") not in o and o[("word", "家")] == "independent"


def test_build_drops_misparsed_pieces_of_kana_entries(monkeypatch):
    from server.learner import anki, profile as P
    note = iter(range(1, 100))

    def card(fields, deck, typ=2):
        return {"note": next(note), "fields": {k: {"value": v} for k, v in fields.items()},
                "type": typ, "interval": 30, "deckName": deck}
    decks = {
        "Core": [card({"W": "行く", "R": "いく", "M": "go"}, "Core")],
        "JFBP": [card({"K": k, "E": ""}, "JFBP::Lesson 3", typ=0)
                 for k in ("かいしゃ", "よやく", "いきます", "ひるごはん", "ひる", "ごはん")],
    }
    monkeypatch.setattr(anki, "deck_cards", lambda deck, url=None: decks[deck])
    cfg = {"textbook": "jfbp-r4-kana", "lesson": 10, "sources": [
        {"deck": "Core", "word_field": "W", "reading_field": "R", "meaning_field": "M",
         "include": "studied", "kanji": True},
        {"deck": "JFBP", "word_field": "K", "meaning_field": "E", "include": "lessons",
         "lesson_pattern": r"Lesson (\d+)"}]}
    readings = {w["reading"] for w in P.build(cfg)["words"]}
    assert {"かいしゃ", "よやく", "いく", "ひる", "ごはん"} <= readings
    assert "いしゃ" not in readings and "やく" not in readings     # misparse leftovers


def test_chat_bubbles_keep_all_kanji_with_furigana(profile):
    # 銀行 isn't a learned kanji word, but bubbles keep it, with its reading as furigana
    r = display.render("銀行は9時からです。", profile, all_kanji=True)
    assert r.display == "銀行は9時からです。"
    ruby = [seg for t in display.kaiwa_tokens(r) for seg in t["ruby"] if seg["r"]]
    assert {"t": "銀行", "r": "ぎんこう"} in ruby
    # the model's reading still fixes UniDic's misreadings in the furigana
    r = display.render("富士山に行きました。", profile, reading="ふじさんにいきました。", all_kanji=True)
    assert r.display == "富士山に行きました。"
    assert {"t": "山", "r": "さん"} in [s for t in display.kaiwa_tokens(r) for s in t["ruby"]]


def test_ime_single_kanji_by_attested_reading():
    if not ime._kanjidic():
        pytest.skip("KANJIDIC2 not downloaded (./setup.sh)")
    snap = {"textbook": "jfbp-r4-kana", "lesson": 10, "grammar": [], "words": [], "phrases": [],
            "kanji_words": {"来年": "Core 2000", "来る": "Core 2000"}}
    t = ime.table(Profile(snap))
    assert t["らいねん"] == ["来年"]
    assert "来" in t["らい"]               # attested by 来年, so 来週 can be typed らい+しゅう
    assert "来" not in t.get("たい", [])   # a rare reading of 来, not attested: left out


def test_spaced_words_trusts_the_textbook_spacing():
    from server.learner.profile import spaced_words
    assert spaced_words("いかがですか。") == {"いかが"}
    assert spaced_words("あまり テレビを みません") == {"あまり", "てれび", "みる"}
    assert spaced_words("ぜんぜん...〜ません") == {"ぜんぜん"}
    assert spaced_words("おじゃまします") == {"じゃま"}               # past the お prefix
    assert spaced_words("オレンジジュース") == {"おれんじ", "じゅーす"}  # loanword compound
    assert spaced_words("かいしゃ") == set() and spaced_words("よやく") == set()   # misparses


def test_split_compounds_are_one_tracked_word(monkeypatch):
    from server.learner import anki, production, profile as P
    note = iter(range(1, 100))

    def card(w, r, m):
        return {"note": next(note), "fields": {"W": {"value": w}, "R": {"value": r}, "M": {"value": m}},
                "type": 2, "interval": 30, "deckName": "Core"}
    decks = {"Core": [card("土曜日", "どようび", "Saturday"), card("友達", "ともだち", "friend")]}
    monkeypatch.setattr(anki, "deck_cards", lambda deck, url=None: decks[deck])
    cfg = {"textbook": "jfbp-r4-kana", "lesson": 10, "sources": [
        {"deck": "Core", "word_field": "W", "reading_field": "R", "meaning_field": "M",
         "include": "studied", "kanji": True}]}
    p = Profile(P.build(cfg))
    assert "土曜日" in p.word_list() and "土曜" not in p.word_list()   # 土曜日 → 土曜 + 日
    assert p.lemmas["土曜日"]["meaning"] == "Saturday"
    assert validator.validate("土曜日に友達と行きます。", p).violations[0].text == "行き"  # pieces license
    obs = production.observe("土曜日に友達と。", p)
    assert {(o.item, o.surface) for o in obs if o.kind == "word"} == {("土曜日", "土曜日"), ("友達", "友達")}


def test_card_readings_track_as_the_same_word():
    from server.learner import production
    snap = {"textbook": "jfbp-r4-kana", "lesson": 10, "grammar": [], "phrases": [],
            "words": [_word("家", "いえ"), _word("reading:うち", "うち"), _word("百", "ひゃく"),
                      _word("reading:ひゃく", "ひゃく"), _word("reading:とき", "とき")],
            "kanji_words": {"家": "Core 2000"},
            "kanji_readings": {"家": "うち", "百": "ひゃく", "時": "とき"}}
    p = Profile(snap)
    said = lambda text: {o.item for o in production.observe(text, p) if o.kind == "word"}
    assert said("うち") == said("家") == {"家"}        # UniDic reads 家 いえ, the card うち
    assert not {"reading:うち", "百", "reading:ひゃく", "reading:とき"} & set(p.word_list())
