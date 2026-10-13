"""Speaking practice scheduling: which words and grammar to steer the next
conversation toward.

Each item's schedule is replayed from its observation history (production.py),
so there's no separate state to drift out of sync:
  independent use  → the interval grows (1 day, then ×2.5 …); several uses in
                     one sitting count once
  error            → the interval shrinks, so it comes back soon
  echo / hint      → exposure only, no change
Targets for a conversation: recent mistakes first, then overdue items, then
items studied but never said (rotated so sessions differ).
"""
import random
from dataclasses import dataclass

DAY = 86400.0
FIRST_INTERVAL = 1.0      # days after the first independent use
GROWTH = 2.5
MAX_INTERVAL = 120.0
ERROR_FACTOR = 0.4
MIN_INTERVAL = 0.5
SITTING = 0.5 * DAY       # uses closer together than this count once


@dataclass
class ItemState:
    successes: int = 0
    errors: int = 0
    interval: float = 0.0     # days
    last: float = 0.0         # last scheduling event (independent use or error)
    last_error: float = 0.0

    @property
    def due(self) -> float:
        """When it should come back; 0 = never produced (always eligible)."""
        return self.last + self.interval * DAY if (self.successes or self.errors) else 0.0


def replay(events) -> dict:
    """events: iterable of (kind, item, outcome, ts) → {(kind, item): ItemState}."""
    states: dict = {}
    for kind, item, outcome, ts in sorted(events, key=lambda e: e[3]):
        st = states.setdefault((kind, item), ItemState())
        if outcome == "independent":
            erred_since = st.errors and st.last_error >= st.last
            if st.successes and ts - st.last < SITTING and not erred_since:
                continue                              # same sitting: counted already
            st.interval = FIRST_INTERVAL if not st.interval else min(st.interval * GROWTH, MAX_INTERVAL)
            st.successes += 1
            st.last = ts
        elif outcome == "error":
            st.errors += 1
            st.last_error = ts
            st.interval = max(MIN_INTERVAL, st.interval * ERROR_FACTOR) if st.interval else MIN_INTERVAL
            st.last = ts
    return states


def pick(candidates: list, states: dict, now: float, n: int, rng: random.Random | None = None) -> list:
    """Choose up to n of candidates [(kind, item), …] in priority order.
    Returns [(key, reason)] with reason "recent mistake" | "due" | "never said"."""
    rng = rng or random.Random()
    mistakes, due, never = [], [], []
    for key in candidates:
        st = states.get(key)
        if st is None or st.successes == 0:
            if st and st.errors:
                mistakes.append((st.last_error, key))
            else:
                never.append(key)
        elif st.due <= now:
            if st.errors and st.last_error >= st.last:
                mistakes.append((st.last_error, key))
            else:
                due.append(((now - st.due) / (st.interval * DAY), key))   # most overdue first
    out = [(k, "recent mistake") for _, k in sorted(mistakes, reverse=True)]
    out += [(k, "due") for _, k in sorted(due, reverse=True)]
    # never said: rotate through the front of the (already prioritized) list
    pool = never[: max(n * 3, 12)]
    out += [(k, "never said") for k in rng.sample(pool, len(pool))]
    return out[:n]


def report(targets: list, session_obs) -> list:
    """How each target went in a session. session_obs: (kind, item, outcome) rows."""
    seen: dict = {}
    for kind, item, outcome in session_obs:
        seen.setdefault((kind, item), set()).add(outcome)
    out = []
    for t in targets:
        outcomes = seen.get((t["kind"], t["item"]), set())
        result = ("hit" if "independent" in outcomes else
                  "tried" if "error" in outcomes else
                  "assisted" if outcomes & {"echo", "hint"} else "missed")
        out.append({**t, "result": result})
    return out
