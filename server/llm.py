"""Claude access for Kaiwa: API key and model settings, plus a JSON helper for
the analysis tasks (corrections, hints, translations, word notes, summaries).

Conversation turns don't come through here — they're generated and validated
by learner.generate (see learner/service.py).
"""
import json
import os

import anthropic

from .learner import generate
from .learner.grammar import ROOT

MODELS = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]  # first = default


def load_env():
    """Read KEY=value lines from ./.env into the environment (never overriding it)."""
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)


load_env()


def api_key(settings: dict) -> str:
    """Key saved in Settings, else ANTHROPIC_API_KEY from the environment / .env."""
    return (settings.get("api_key_anthropic") or "").strip() or os.environ.get("ANTHROPIC_API_KEY", "")


def model(settings: dict) -> str:
    m = settings.get("claude_model")
    return m if m in MODELS else MODELS[0]


def client(settings: dict) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key(settings))


def not_ready_reason(settings: dict) -> str | None:
    if not api_key(settings):
        return "No Anthropic API key — add one in Settings (or ANTHROPIC_API_KEY in .env)"
    return None


def _parse_json(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # salvage the first {...} block if the model added stray text
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                pass
        return {}


def chat_json(prompt: str, settings: dict, max_tokens: int = 4000) -> dict:
    """One-shot JSON task. Effort low: these are short, latency-sensitive jobs."""
    m = model(settings)
    resp = generate.create_message(
        client(settings), m, max_tokens=max_tokens,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json(next((b.text for b in resp.content if b.type == "text"), "{}"))
