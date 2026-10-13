"""SQLite persistence for Kaiwa: profile, sessions, messages, mistakes.

What the learner knows lives in the learner profile (data/learner_profile.json),
not here; flashcards stay in Anki."""
import json
import os
import sqlite3
import time
from datetime import date, timedelta

# KAIWA_DB points at another database (tests, experiments) without touching yours.
DB_PATH = os.environ.get("KAIWA_DB") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "kaiwa.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT DEFAULT '',
    interests TEXT DEFAULT '',
    settings TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    scenario_id TEXT,
    scenario TEXT,             -- JSON of scenario/custom roleplay used
    started_at REAL NOT NULL,
    ended_at REAL,
    summary TEXT,              -- JSON summary
    targets TEXT               -- JSON practice targets (learner/practice.py)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    translation TEXT,
    correction TEXT,           -- JSON correction result (user messages)
    meta TEXT,                 -- JSON generation details (learner-constrained replies)
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (  -- what the learner produced (learner/production.py)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    kind TEXT NOT NULL,        -- word | grammar
    item TEXT NOT NULL,        -- word id or grammar concept id
    outcome TEXT NOT NULL,     -- independent | echo | hint | error
    channel TEXT NOT NULL,     -- text | voice
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS observations_item ON observations(kind, item);
CREATE TABLE IF NOT EXISTS mistakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    original TEXT,
    corrected TEXT,
    explanation TEXT,
    category TEXT,
    ts REAL NOT NULL
);
"""


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.executescript(SCHEMA)
        c.execute("INSERT OR IGNORE INTO profile (id) VALUES (1)")
        # databases created before the meta column existed
        # databases created before these columns existed
        cols = {r["name"] for r in c.execute("PRAGMA table_info(messages)")}
        if "meta" not in cols:
            c.execute("ALTER TABLE messages ADD COLUMN meta TEXT")
        cols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)")}
        if "targets" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN targets TEXT")


# ------------------------------------------------------------------ profile

def get_profile() -> dict:
    with _conn() as c:
        r = c.execute("SELECT * FROM profile WHERE id=1").fetchone()
        d = dict(r)
        d["settings"] = json.loads(d.get("settings") or "{}")
        return d


def update_profile(fields: dict):
    allowed = {"name", "interests"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
        elif k == "settings":
            sets.append("settings=?")
            vals.append(json.dumps(v))
    if not sets:
        return
    with _conn() as c:
        c.execute(f"UPDATE profile SET {', '.join(sets)} WHERE id=1", vals)


def get_setting(key: str, default=None):
    return get_profile()["settings"].get(key, default)


def set_settings(patch: dict):
    p = get_profile()
    p["settings"].update(patch)
    update_profile({"settings": p["settings"]})


# ----------------------------------------------------------------- sessions

def create_session(mode: str, scenario_id: str | None, scenario: dict | None,
                   targets: list | None = None) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO sessions (mode, scenario_id, scenario, started_at, targets) VALUES (?,?,?,?,?)",
            (mode, scenario_id, json.dumps(scenario) if scenario else None, time.time(),
             json.dumps(targets, ensure_ascii=False) if targets else None),
        )
        return cur.lastrowid


def get_session(sid: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["scenario"] = json.loads(d["scenario"]) if d["scenario"] else None
        d["summary"] = json.loads(d["summary"]) if d["summary"] else None
        d["targets"] = json.loads(d["targets"]) if d.get("targets") else []
        return d


def end_session(sid: int, summary: dict):
    with _conn() as c:
        c.execute("UPDATE sessions SET ended_at=?, summary=? WHERE id=?",
                  (time.time(), json.dumps(summary), sid))


def add_message(sid: int, role: str, text: str, translation: str | None = None,
                meta: dict | None = None) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO messages (session_id, role, text, translation, meta, ts) VALUES (?,?,?,?,?,?)",
            (sid, role, text, translation, json.dumps(meta, ensure_ascii=False) if meta else None,
             time.time()),
        )
        return cur.lastrowid


def get_messages(sid: int) -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id", (sid,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["correction"] = json.loads(d["correction"]) if d["correction"] else None
            d["meta"] = json.loads(d["meta"]) if d.get("meta") else None
            out.append(d)
        return out


def get_message(mid: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["meta"] = json.loads(d["meta"]) if d.get("meta") else None
        d["correction"] = json.loads(d["correction"]) if d.get("correction") else None
        return d


def set_message_translation(mid: int, translation: str):
    with _conn() as c:
        c.execute("UPDATE messages SET translation=? WHERE id=?", (translation, mid))


def set_message_correction(mid: int, correction: dict):
    with _conn() as c:
        c.execute("UPDATE messages SET correction=? WHERE id=?",
                  (json.dumps(correction), mid))


# ------------------------------------------------------------- observations

def set_observations(mid: int, sid: int, channel: str, obs: list):
    """Replace a message's observations (a message is analyzed once, but re-runs stay idempotent)."""
    now = time.time()
    with _conn() as c:
        c.execute("DELETE FROM observations WHERE message_id=?", (mid,))
        c.executemany(
            "INSERT INTO observations (message_id, session_id, kind, item, outcome, channel, ts) "
            "VALUES (?,?,?,?,?,?,?)",
            [(mid, sid, o.kind, o.item, o.outcome, channel, now) for o in obs])


def observation_events() -> list:
    """Every observation as (kind, item, outcome, ts), for practice scheduling."""
    with _conn() as c:
        return [tuple(r) for r in c.execute("SELECT kind, item, outcome, ts FROM observations")]


def session_observations(sid: int) -> list:
    with _conn() as c:
        return [tuple(r) for r in c.execute(
            "SELECT kind, item, outcome FROM observations WHERE session_id=?", (sid,))]


def observation_stats() -> list:
    """Per item: count per outcome and channel, and last independent use."""
    with _conn() as c:
        rows = c.execute(
            "SELECT kind, item, outcome, channel, COUNT(*) n, MAX(ts) last "
            "FROM observations GROUP BY kind, item, outcome, channel").fetchall()
        return [dict(r) for r in rows]


# ----------------------------------------------------------------- mistakes

def add_mistake(sid: int, err: dict):
    with _conn() as c:
        c.execute(
            "INSERT INTO mistakes (session_id, original, corrected, explanation, category, ts) VALUES (?,?,?,?,?,?)",
            (sid, err.get("wrong", ""), err.get("right", ""),
             err.get("explanation", ""), err.get("category", "other"), time.time()),
        )



def mistake_stats() -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT category, COUNT(*) n FROM mistakes GROUP BY category ORDER BY n DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------- dashboard

def dashboard() -> dict:
    with _conn() as c:
        sessions = c.execute(
            "SELECT id, mode, scenario, started_at, ended_at, summary FROM sessions "
            "WHERE ended_at IS NOT NULL ORDER BY id DESC"
        ).fetchall()
        total_seconds = sum((s["ended_at"] - s["started_at"]) for s in sessions)
        n_mistakes = c.execute("SELECT COUNT(*) n FROM mistakes").fetchone()["n"]
        n_msgs = c.execute("SELECT COUNT(*) n FROM messages WHERE role='user'").fetchone()["n"]

        # streak: consecutive days (ending today or yesterday) with a session
        days = sorted({date.fromtimestamp(s["started_at"]) for s in sessions}, reverse=True)
        streak = 0
        if days:
            cursor = date.today()
            if days[0] in (cursor, cursor - timedelta(days=1)):
                cursor = days[0]
                for d in days:
                    if d == cursor:
                        streak += 1
                        cursor -= timedelta(days=1)
                    elif d < cursor:
                        break

        recent = []
        for s in sessions[:8]:
            scen = json.loads(s["scenario"]) if s["scenario"] else None
            summ = json.loads(s["summary"]) if s["summary"] else None
            recent.append({
                "id": s["id"], "mode": s["mode"],
                "title": (scen or {}).get(
                    "title", "Voice Call" if s["mode"] == "call" else "Free Chat"),  # "call": older sessions
                "started_at": s["started_at"],
                "minutes": round((s["ended_at"] - s["started_at"]) / 60, 1),
                "summary": summ,
            })
        return {
            "streak": streak,
            "total_minutes": round(total_seconds / 60),
            "sessions_count": len(sessions),
            "messages_spoken": n_msgs,
            "mistakes_logged": n_mistakes,
            "recent_sessions": recent,
            "mistake_categories": mistake_stats(),
        }
