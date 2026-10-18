"""Read vocabulary from Anki through the AnkiConnect add-on."""
import json
import re

import requests

DEFAULT_URL = "http://127.0.0.1:8765"


class AnkiError(RuntimeError):
    pass


def call(action: str, url: str = DEFAULT_URL, **params):
    try:
        r = requests.post(url, json={"action": action, "version": 6, "params": params}, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        raise AnkiError(f"can't reach AnkiConnect at {url} — is Anki running? ({e})") from e
    body = r.json()
    if body.get("error"):
        raise AnkiError(body["error"])
    return body["result"]


def card_tier(card: dict) -> str:
    """Familiarity from scheduling state: learning < familiar < established."""
    if card["type"] == 0:
        return "new"
    if card["type"] in (1, 3):  # learning / relearning
        return "learning"
    return "established" if card["interval"] >= 21 else "familiar"


def deck_cards(deck: str, url: str = DEFAULT_URL) -> list:
    """cardsInfo for every card in `deck` and its subdecks."""
    ids = call("findCards", url, query=f'deck:"{deck}"')
    out = []
    for k in range(0, len(ids), 500):
        out += call("cardsInfo", url, cards=ids[k:k + 500])
    return out


_TAG = re.compile(r"<[^>]+>")
_FURIGANA = re.compile(r"\s?([^\s\[\]]+)\[[^\]]*\]")  # Anki "漢字[かんじ]" → 漢字


def clean_field(value: str) -> str:
    v = _TAG.sub("", value)
    v = v.replace("&nbsp;", " ").replace("&amp;", "&")
    v = _FURIGANA.sub(r"\1", v)
    return v.strip()


def lesson_of(deck_name: str, pattern: str) -> int | None:
    """Highest lesson number in a subdeck name ("…::Lesson 9-10" → 10)."""
    m = re.search(pattern, deck_name.split("::")[-1])
    if not m:
        return None
    return max(int(g) for g in m.groups() if g)


if __name__ == "__main__":
    print(json.dumps(call("deckNames"), ensure_ascii=False))
