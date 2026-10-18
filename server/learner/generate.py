"""Constrained conversation generation: Claude drafts, the validator checks,
and failed drafts get targeted feedback for a rewrite.

The learner model — not the LLM — decides what's allowed. Claude sees the
allowed vocabulary and grammar in a cached system prompt, but every reply is
validated before it's shown, and violations are fed back as a mid-conversation
system message so the cached prefix stays intact.
"""
import json
import os
import re
import time
from dataclasses import dataclass, field

import anthropic
import yaml

from . import display, grammar, morph, validator
from .profile import Profile

# $/MTok: input, output, cache read. Cache writes bill at 1.25x input.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
# Server-side refusal fallback is available on Opus/Sonnet, not Haiku.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "japanese": {"type": "string"},
        "reading": {"type": "string"},
        "english": {"type": "string"},
    },
    "required": ["japanese", "reading", "english"],
    "additionalProperties": False,
}


def create_message(client, model: str, **kwargs):
    """messages.create with server-side refusal fallback where the model supports it."""
    if model in FALLBACK_MODELS:
        resp = client.beta.messages.create(
            model=model, betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    else:
        resp = client.messages.create(model=model, **kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"model refused: {getattr(resp, 'stop_details', None)}")
    return resp


def load_scenarios() -> dict:
    with open(os.path.join(grammar.CONTENT, "scenarios", "phase1.yaml"), encoding="utf-8") as f:
        return {s["id"]: s for s in yaml.safe_load(f)["scenarios"]}


def _load_closed() -> list:
    with open(os.path.join(grammar.CONTENT, "closed_class.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)["words"]


def partner_system_prompt(profile: Profile, concepts: dict) -> str:
    """Learner-specific, scenario-independent: identical across a learner's
    conversations so it caches."""
    lines = [
        "You are a Japanese conversation partner for a beginner learner. You play a role in a "
        "short everyday scene and keep the conversation going in Japanese.",
        "",
        "The learner can only understand the vocabulary and grammar listed below. Every reply "
        "you write is checked automatically; anything outside these lists is rejected and you "
        "will be asked to rewrite it.",
        "",
        "How to reply:",
        "- One to three short sentences.",
        "- Be a person, not an interviewer — but share only what someone in your role would, "
        "the way it works in Japan:",
        "  - A friend or language-exchange partner shares freely: work, family, likes, plans, "
        "what they did yesterday (make it up as needed and keep it consistent). React with "
        "your own side (「私も日曜日に映画を見ました。」) and offer bits the learner can pick up, "
        "ask about, or take somewhere. About half your turns end on a simple question, the "
        "rest on something about you the learner can respond to.",
        "  - A coworker, neighbor, or someone just met: polite small talk, modest about "
        "yourself, nothing too personal.",
        "  - Staff at work (shop clerk, pharmacist, receptionist, station staff): stay "
        "professional and focused on helping the customer — answer, recommend, explain, ask "
        "what they need; at most a friendly word about the weather or the shop. Don't talk "
        "about your own life.",
        "  - Speak as that person would: staff use the polite service phrasing customers "
        "expect where the learner knows it; friends are warmer and more relaxed (still です"
        "／ます).",
        "    Mind set phrases: いらっしゃいませ is only staff greeting customers (a host at home "
        "says いらっしゃい or どうぞ); もしもし is for personal phone calls, not a business "
        "answering.",
        "- At most one question per turn. Share one thing at a time, connected to what you're "
        "talking about. Like a native speaker, drop 私は when it's clear who you mean.",
        "- When the learner asks you something, answer properly, with a little detail.",
        "- When you do ask, follow up on what the learner just said rather than switching "
        "topics.",
        "- Polite です／ます style.",
        "- Write standard Japanese spelling with kanji where normal; the app adds furigana. "
        "No romaji, no English, no furigana in the Japanese.",
        "- Write numbers with Arabic digits: 3時, 2,000円, 4人.",
        "- Use only words from the vocabulary list, the always-allowed words, numbers, and "
        "people's or places' names. Conjugate only with the grammar listed.",
        "- If what you want to say needs other words or grammar, say something simpler. A "
        "natural, simple reply beats an ambitious one.",
        "- If the learner makes a mistake, don't correct them explicitly; reply naturally, "
        "using the correct form yourself where it fits.",
        "- \"reading\" is the same Japanese written entirely in hiragana (katakana words stay "
        "in katakana, digits stay digits), read the way a native speaker would say it.",
        "- \"english\" is a natural English translation of your Japanese.",
        "",
        "GRAMMAR THE LEARNER KNOWS (anything else is off-limits — e.g. no て-form, no plain "
        "forms, no ～たい, no ～ましょう unless listed):",
    ]
    for cid in sorted(profile.grammar):
        c = concepts.get(cid)
        if c:
            lines.append(f"- {c['name']}: {c['gloss']} e.g. {c['example']}")
    lines += [
        "",
        "ALWAYS-ALLOWED WORDS: " + "、".join(_load_closed()),
        "",
        "VOCABULARY THE LEARNER KNOWS (dictionary forms; readings in parentheses; kana-only "
        "entries may be written in kanji if that's the normal spelling):",
        "、".join(profile.vocab_for_prompt()),
    ]
    return "\n".join(lines)


def scenario_prompt(scenario: dict) -> str:
    return (f"SCENE: {scenario['title']}\n"
            f"Setting: {scenario['setting']}\n"
            f"Your role: {scenario['ai_role']}\n"
            f"The learner's role: {scenario['user_role']}\n"
            f"Goal of the conversation: {scenario['goal']}\n"
            "Stay in the scene and move it toward the goal. Keep it consistent with what has "
            "already been said, and don't ask what the learner already told you."
            + "".join(f"\n{n}" for n in scenario.get("notes", [])))


def repair_message(drafts: list) -> str:
    lines = ["Your reply was rejected because it uses language the learner hasn't learned."]
    for d in drafts:
        lines.append(f"\nDraft: {d['japanese']}")
        lines += [f"- {v}" for v in d["violations"]]
    lines.append("\nWrite a new reply that avoids every problem above. Replace unknown words "
                 "with known ones or rephrase more simply; don't just drop needed words.")
    return "\n".join(lines)


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    calls: int = 0

    def add(self, u):
        self.input += u.input_tokens or 0
        self.output += u.output_tokens or 0
        self.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.calls += 1

    def cost(self, model: str) -> float:
        pin, pout, pread = PRICES.get(model, (0, 0, 0))
        return (self.input * pin + self.cache_write * pin * 1.25
                + self.cache_read * pread + self.output * pout) / 1e6


@dataclass
class TurnResult:
    japanese: str
    english: str
    ok: bool
    display: str = ""                              # what the learner sees (known kanji only)
    attempts: list = field(default_factory=list)   # [{japanese, english, violations, seconds}]
    seconds: float = 0.0
    proper_nouns: list = field(default_factory=list)
    reading_fixes: list = field(default_factory=list)  # UniDic readings overridden by the model's
    salvaged: bool = False   # no draft passed whole; shown text = the sentences that did


class Partner:
    """The AI side of one conversation."""

    def __init__(self, profile: Profile, scenario: dict, model: str = "claude-opus-5-5",
                 effort: str = "low", max_attempts: int = 3, client=None, concepts=None,
                 avoid: set | None = None):
        """avoid: word ids the partner must not say (practice targets the learner
        hasn't produced yet — if the partner says them first, the learner copies)."""
        self.profile = profile
        self.avoid = set(avoid or ())
        self.model = model
        self.effort = effort
        self.max_attempts = max_attempts
        self.client = client or anthropic.Anthropic()
        self.concepts = concepts or grammar.load_concepts()
        self.system = [
            {"type": "text", "text": partner_system_prompt(profile, self.concepts),
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": scenario_prompt(scenario)},
        ]
        self.history = [{"role": "user", "content": "(The scene begins. Say your first line.)"}]
        self.usage = Usage()

    def learner_says(self, text: str):
        self.history.append({"role": "user", "content": text})

    def _draft(self, extra: list) -> dict:
        resp = create_message(
            self.client, self.model,
            max_tokens=4000,
            system=self.system,
            messages=self.history + extra,
            output_config={"effort": self.effort,
                           "format": {"type": "json_schema", "schema": REPLY_SCHEMA}},
        )
        self.usage.add(resp.usage)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"japanese": text.strip(), "reading": "", "english": ""}

    def ai_turn(self) -> TurnResult:
        drafts, t0 = [], time.monotonic()
        best = None
        for _ in range(self.max_attempts):
            extra = [{"role": "system", "content": repair_message(drafts)}] if drafts else []
            ts = time.monotonic()
            d = self._draft(extra)
            r = validator.validate(d["japanese"], self.profile, self.concepts)
            d["violations"] = [str(v) for v in r.violations] + self._target_violations(d["japanese"])
            d["seconds"] = round(time.monotonic() - ts, 2)
            d["proper_nouns"] = r.proper_nouns
            drafts.append(d)
            if best is None or len(d["violations"]) < len(best["violations"]):
                best = d
            if not d["violations"]:
                break
        if best["violations"]:
            # No draft passed whole. Drafts are a few short sentences and usually
            # only one breaks the rules, so show the sentences that do pass.
            text = self._salvage(sorted(drafts, key=lambda d: len(d["violations"])))
            self.history.append({"role": "assistant", "content": text})
            shown = display.render(text, self.profile)
            return TurnResult(text, "", True, shown.display, drafts,
                              round(time.monotonic() - t0, 2), [], [], salvaged=True)
        self.history.append({"role": "assistant", "content": best["japanese"]})
        shown = display.render(best["japanese"], self.profile, best.get("reading"))
        return TurnResult(best["japanese"], best["english"], True, shown.display,
                          drafts, round(time.monotonic() - t0, 2), best["proper_nouns"],
                          shown.reading_fixes)

    def _target_violations(self, text: str) -> list:
        if not self.avoid:
            return []
        toks = morph.tokenize(text)
        said = {w: "".join(t.surface for t in toks[i:j]) for i, j, w in self.profile.word_spans(toks)}
        return [f"[target] 「{said[w]}」 is a practice target — don't say it; ask so the learner says it"
                for w in self.avoid if w in said]

    def _salvage(self, drafts: list) -> str:
        """Valid sentences from the least-bad draft, plus a valid question from
        another draft if it has none; そうですか。 if nothing passes."""
        def ok_sentences(d):
            parts = re.findall(r"[^。！？!?]+[。！？!?]?", d["japanese"])
            return [p.strip() for p in parts if p.strip()
                    and validator.validate(p.strip(), self.profile, self.concepts).ok
                    and not self._target_violations(p.strip())]
        keep = ok_sentences(drafts[0])
        backchannel = {"そうですか。", "そうですか", "そうですか？"}       # "I see" — not a question
        is_q = lambda s: s.endswith(("か。", "？", "?", "か")) and s not in backchannel
        if not any(is_q(s) for s in keep):
            q = next((s for d in drafts[1:] for s in ok_sentences(d) if is_q(s) and s not in keep), None)
            if q:
                keep.append(q)
        return "".join(keep) or "そうですか。"
