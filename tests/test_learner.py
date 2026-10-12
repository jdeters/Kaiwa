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
                            "何": "Core 2000"}}
    t = ime.table(Profile(snap))
    assert t["たべ"] == ["食べ"]
    assert t["たかく"] == ["高く"] and t["たかか"] == ["高か"]    # 高くない / 高かった
    assert t["き"] == ["来"] and t["こ"] == ["来"]
    assert "何" in t["なに"] and "何" in t["なん"]


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
