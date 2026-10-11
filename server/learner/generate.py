"""Constrained conversation generation: Claude drafts, the validator checks,
and failed drafts get targeted feedback for a rewrite.

The learner model — not the LLM — decides what's allowed. Claude sees the
allowed vocabulary and grammar in a cached system prompt, but every reply is
validated before it's shown, and violations are fed back as a mid-conversation
system message so the cached prefix stays intact.
"""
import json
import os
import time
from dataclasses import dataclass, field

import anthropic
import yaml

from . import display, grammar, validator
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
        "- One or two short sentences, then usually a simple question the learner can answer "
        "with what they know.",
        "- Polite です／ます style.",
        "- Write standard Japanese spelling with kanji where normal; the app shows kanji only "
        "for words the learner can read and converts the rest to kana. No romaji, no English, "
        "no furigana in the Japanese.",
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
            "Stay in the scene and move it toward the goal."
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


class Partner:
    """The AI side of one conversation."""

    def __init__(self, profile: Profile, scenario: dict, model: str = "claude-opus-5-5",
                 effort: str = "low", max_attempts: int = 3, client=None, concepts=None):
        self.profile = profile
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
            d["violations"] = [str(v) for v in r.violations]
            d["seconds"] = round(time.monotonic() - ts, 2)
            d["proper_nouns"] = r.proper_nouns
            drafts.append(d)
            if best is None or len(d["violations"]) < len(best["violations"]):
                best = d
            if r.ok:
                break
        # Phase 1 measures failures rather than hiding them: if no draft passed,
        # the least-bad one is used and the turn is marked not ok.
        self.history.append({"role": "assistant", "content": best["japanese"]})
        shown = display.render(best["japanese"], self.profile, best.get("reading"))
        return TurnResult(best["japanese"], best["english"], not best["violations"], shown.display,
                          drafts, round(time.monotonic() - t0, 2), best["proper_nouns"],
                          shown.reading_fixes)
