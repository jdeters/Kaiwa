"""Kaiwa — Japanese conversation practice that stays within what you've learned.

Conversation: learner model + Claude (server/learner). STT: faster-whisper ·
TTS: VOICEVOX / AivisSpeech / macOS say.
"""
import json
import os
import threading
from datetime import date

from fastapi import Body, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import backup, db, dictionary, jp, llm, phone, prompts, scenarios, stt, tts
from .learner import anki
from .learner import service as learner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
MODES = ("free_chat", "roleplay")   # voice is a mode of any chat, not a session type

app = FastAPI(title="Kaiwa")
db.init()
backup.start_scheduler()
threading.Thread(target=stt.warm, daemon=True).start()  # load whisper before the first utterance


def settings() -> dict:
    return db.get_profile()["settings"]


def setting(key, default=None):
    return db.get_setting(key, default)


# ------------------------------------------------------------------- static

@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


app.mount("/static", StaticFiles(directory=WEB), name="static")


# ------------------------------------------------------------------- status

@app.get("/api/health")
def health():
    s = settings()
    return {
        "claude": llm.not_ready_reason(s) is None,
        "model": llm.model(s),
        "learner": learner.status(),
        "whisper": stt.available(),
        "voicevox": tts.voicevox_up(),
        "aivis": tts.engine_up("aivis"),
        "dictionary": dictionary.available(),
    }


@app.get("/api/ai")
def ai_info():
    """Model choices for Settings (never returns the key itself)."""
    s = settings()
    return {"models": llm.MODELS, "model": llm.model(s), "has_key": bool(llm.api_key(s)),
            "key_saved": bool((s.get("api_key_anthropic") or "").strip())}


@app.get("/api/learner")
def learner_status():
    return learner.status()


@app.post("/api/learner/build")
def learner_build(body: dict = Body(...)):
    """Update the learner profile from Anki; body may set {"lesson": n}."""
    try:
        return learner.rebuild(body.get("lesson"))
    except anki.AnkiError as e:
        msg = ("Couldn't reach Anki — open Anki (with AnkiConnect installed) and try again."
               if "can't reach AnkiConnect" in str(e) else f"Anki error: {e}")
        return JSONResponse({"error": msg}, status_code=503)


@app.get("/api/ime")
def ime_table():
    """Kana → kanji suggestions for typing, limited to kanji the learner has learned."""
    return {"table": learner.ime_table()}


@app.get("/api/setup/phone")
def setup_phone():
    return phone.phone_info()


# ------------------------------------------------------------------ profile

def _safe_profile() -> dict:
    """Profile with the API key stripped — the UI only needs to know it exists (/api/ai)."""
    p = db.get_profile()
    p["settings"] = {k: v for k, v in p["settings"].items() if not k.startswith("api_key_")}
    return p


@app.get("/api/profile")
def get_profile():
    p = _safe_profile()
    s = p["settings"]
    s.setdefault("voice", tts.default_voice())
    s.setdefault("speed", 1.0)
    s.setdefault("intonation", 1.0)
    s.setdefault("auto_play", True)
    s.setdefault("furigana", True)
    s.setdefault("romaji", False)
    s.setdefault("auto_translate", False)
    s.setdefault("romaji_input", True)
    return p


@app.put("/api/profile")
async def put_profile(req: Request):
    db.update_profile(await req.json())
    return _safe_profile()


@app.post("/api/settings")
async def post_settings(req: Request):
    db.set_settings(await req.json())
    return _safe_profile()["settings"]


@app.get("/api/voices")
def voices():
    return {"voices": tts.list_voices(), "default": tts.default_voice()}


# ---------------------------------------------------------------- scenarios

@app.get("/api/scenarios")
def get_scenarios():
    return scenarios.catalog()


# ----------------------------------------------------------------- sessions

@app.post("/api/sessions")
async def create_session(req: Request):
    body = await req.json()
    mode = body.get("mode", "free_chat")
    if mode not in MODES:
        return JSONResponse({"error": f"unknown mode: {mode}"}, status_code=400)
    scen, sid_key = None, None
    if mode == "roleplay":
        if body.get("scenario_id"):
            scen = scenarios.get(body["scenario_id"])
            sid_key = body["scenario_id"]
        elif body.get("custom"):
            c = body["custom"]
            scen = {
                "id": "custom", "title": c.get("title", "Custom Roleplay"),
                "description": c.get("description", ""),
                "ai_role": c.get("ai_role", ""), "user_role": c.get("user_role", ""),
                "setting": c.get("setting", ""), "kind": "roleplay",
            }
            sid_key = "custom"
    targets = learner.choose_targets(db.observation_events()) if learner.profile() else []
    session_id = db.create_session(mode, sid_key, scen, targets)
    return {"session_id": session_id, "scenario": scen}


@app.get("/api/sessions/{sid}/messages")
def session_messages(sid: int):
    msgs = db.get_messages(sid)
    have_profile = learner.profile() is not None
    for m in msgs:
        if m["role"] == "assistant":
            m["tokens"] = (learner.tokens(m["text"], (m.get("meta") or {}).get("reading"))
                           if have_profile else jp.annotate(m["text"]))
            m["romaji"] = jp.romaji(m["text"])
    return {"messages": msgs}


@app.post("/api/chat")
async def chat(req: Request):
    """One partner turn as SSE. The reply is generated, validated against the
    learner profile, and repaired before anything is sent, so it arrives whole:
    `speak` is the kanji original (TTS, lookups), `tokens`/`display` show known
    kanji only. Empty text lets the partner open the scene."""
    body = await req.json()
    sid = body["session_id"]
    text = (body.get("text") or "").strip()
    session = db.get_session(sid)
    if not session:
        return JSONResponse({"error": "no such session"}, status_code=404)
    profile = db.get_profile()
    err = (None if learner.profile() else learner.NO_PROFILE) or llm.not_ready_reason(profile["settings"])
    if err:
        return JSONResponse({"error": err}, status_code=503)
    channel = "voice" if body.get("channel") == "voice" else "text"
    user_msg_id = (db.add_message(sid, "user", text, meta={"channel": channel, "hint": bool(body.get("hint"))})
                   if text else None)

    def gen():
        yield f"data: {json.dumps({'user_message_id': user_msg_id})}\n\n"
        s = profile["settings"]
        try:
            r = learner.reply(session, db.get_messages(sid), profile, llm.client(s), llm.model(s),
                              db.session_observations(sid))
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return
        mid = db.add_message(sid, "assistant", r["japanese"], translation=r["english"],
                             meta=r["meta"])
        final = {
            "done": True, "message_id": mid, "tokens": r["tokens"],
            "romaji": jp.romaji(r["japanese"]), "speak": r["japanese"], "display": r["display"],
        }
        yield f"data: {json.dumps(final)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------- analysis (LLM jobs)

# These call Claude, so they're plain (sync) handlers: FastAPI runs them in a
# worker thread instead of blocking the event loop (and every other request).

_correct_locks: dict = {}
_correct_locks_guard = threading.Lock()


def _correct_message(msg: dict) -> dict:
    """Correct a learner message once, and record what it produced. The browser
    asks after each message and the session report asks for any still pending;
    the per-message lock makes the second caller reuse the first result."""
    with _correct_locks_guard:
        lock = _correct_locks.setdefault(msg["id"], threading.Lock())
    with lock:
        current = db.get_message(msg["id"])
        if current and current.get("correction"):
            return current["correction"]
        result = llm.chat_json(
            prompts.CORRECTION_PROMPT.format(learner=learner.describe(), text=msg["text"]), settings())
        result.setdefault("has_errors", False)
        result.setdefault("errors", [])
        # corrected Japanese follows the same kanji rule as everything else
        result["corrected"] = learner.show(result.get("corrected", ""))
        for e in result["errors"]:
            e["right"] = learner.show(e.get("right", ""))
            e["explanation"] = learner.show_quoted(e.get("explanation", ""))
        result["praise"] = learner.show_quoted(result.get("praise", ""))
        normalized = result.pop("normalized", "") or msg["text"]   # internal: kanji spelling for analysis
        db.set_message_correction(msg["id"], result)
        if result.get("has_errors"):
            for e in result["errors"][:3]:
                db.add_mistake(msg["session_id"], e)
        _record_production(msg, normalized, result["errors"])
        return result


@app.post("/api/correct")
def correct(body: dict = Body(...)):
    msg = db.get_message(body["message_id"])
    if not msg:
        return JSONResponse({"error": "no such message"}, status_code=404)
    return _correct_message(msg)


def _record_production(msg: dict, normalized: str, errors: list):
    """Track which known words and grammar the learner produced in this message."""
    if learner.profile() is None:
        return
    meta = msg.get("meta") or {}
    earlier = [m for m in db.get_messages(msg["session_id"]) if m["id"] < msg["id"]]
    prev_partner = next((m["text"] for m in reversed(earlier) if m["role"] == "assistant"), "")
    obs = learner.observe(normalized, prev_partner, errors, hint=bool(meta.get("hint")))
    db.set_observations(msg["id"], msg["session_id"], meta.get("channel", "text"), obs)


@app.post("/api/translate")
def translate(body: dict = Body(...)):
    if body.get("message_id"):
        msg = db.get_message(body["message_id"])
        if msg and msg.get("translation"):
            return {"translation": msg["translation"]}
        text = msg["text"] if msg else ""
    else:
        text = body.get("text", "")
    result = llm.chat_json(prompts.TRANSLATE_PROMPT.format(text=text), settings())
    translation = result.get("translation", "")
    if body.get("message_id") and translation:
        db.set_message_translation(body["message_id"], translation)
    return {"translation": translation}


@app.post("/api/hint")
def hint(body: dict = Body(...)):
    """Suggested replies, filtered to ones the learner could actually say."""
    history = prompts.build_hint_history(db.get_messages(body["session_id"]))
    result = llm.chat_json(prompts.HINT_PROMPT.format(learner=learner.describe(), history=history),
                           settings())
    suggestions = []
    for s in result.get("suggestions", []):
        ja = s.get("japanese", "")
        if ja and learner.check(ja).ok:
            suggestions.append({"japanese": learner.show(ja), "english": s.get("english", ""),
                                "romaji": jp.romaji(ja)})
        if len(suggestions) == 3:
            break
    return {"suggestions": suggestions}


_POS_LABELS = {
    "n": "noun", "v1": "ichidan verb", "v5": "godan verb", "adj-i": "い-adjective",
    "adj-na": "な-adjective", "adv": "adverb", "exp": "expression", "prt": "particle",
    "int": "interjection", "ctr": "counter", "aux-v": "auxiliary verb", "pn": "pronoun",
}


@app.post("/api/word")
def word(body: dict = Body(...)):
    w = body.get("word", "").strip()
    sentence = body.get("sentence", "")
    info = jp.word_info(w)

    # 1) JMdict: instant + accurate. Try surface form, then dictionary form.
    entry = dictionary.lookup(w, info["reading"])
    lemma = jp.lemma_of(w)
    conjugated = False
    if not entry and lemma and lemma != w:
        entry = dictionary.lookup(lemma)
        conjugated = entry is not None
    if entry:
        if conjugated:  # show reading of what they clicked, not the lemma
            entry = {**entry, "reading": info["reading"]}
        pos = entry.get("pos") or ""
        pos_label = ", ".join(_POS_LABELS.get(p, p) for p in pos.split(",") if p)
        notes = pos_label
        if conjugated:  # only when the lookup went through the dictionary form
            notes = f"dictionary form: {learner.show(lemma)}" + (f" · {pos_label}" if pos_label else "")
        return {
            "word": w, "reading": entry["reading"] or info["reading"],
            "romaji": info["romaji"], "meaning": entry["meaning"],
            "notes": notes, "example": "", "example_en": "",
        }

    # 2) Claude fallback for names/phrases JMdict doesn't know.
    result = llm.chat_json(prompts.WORD_PROMPT.format(word=w, sentence=sentence), settings())
    return {
        "word": w,
        "reading": info["reading"],
        "romaji": info["romaji"],
        "meaning": result.get("meaning", ""),
        "notes": learner.show_quoted(result.get("notes", "")),
        "example": learner.show(result.get("example", "")),
        "example_en": result.get("example_en", ""),
    }


@app.post("/api/sessions/{sid}/end")
def end_session(sid: int):
    session = db.get_session(sid)
    if not session:
        return JSONResponse({"error": "no such session"}, status_code=404)
    # corrections still in flight would be missing from the report and the targets
    for m in db.get_messages(sid):
        if m["role"] == "user" and not m.get("correction"):
            try:
                _correct_message(m)
            except Exception:
                pass   # report without it rather than fail the whole session end
    msgs = db.get_messages(sid)
    transcript = "\n".join(
        f"{'Partner' if m['role'] == 'assistant' else 'Student'}: {m['text']}" for m in msgs
    )
    corrections = []
    for m in msgs:
        if m.get("correction") and m["correction"].get("has_errors"):
            for e in m["correction"]["errors"]:
                corrections.append(f"{e.get('wrong')} → {e.get('right')} ({e.get('explanation')})")
    summary = {}
    if any(m["role"] == "user" for m in msgs):
        summary = llm.chat_json(
            prompts.SUMMARY_PROMPT.format(learner=learner.describe(), transcript=transcript[-4000:],
                                          corrections="\n".join(corrections) or "none"),
            settings(),
        )
    summary.setdefault("summary", "Session complete — nice work showing up to practice!")
    summary.setdefault("strengths", [])
    summary.setdefault("areas_to_improve", [])
    summary.setdefault("new_words", [])
    summary["summary"] = learner.show_quoted(summary["summary"])
    for k in ("strengths", "areas_to_improve"):
        summary[k] = [learner.show_quoted(x) for x in summary[k]]
    for w in summary["new_words"]:
        entry = dictionary.lookup(w.get("word", "")) or (
            dictionary.lookup(jp.lemma_of(w.get("word", "")) or ""))
        if entry:  # prefer real dictionary glosses over model guesses
            w["meaning"] = entry["meaning"]
            w["reading"] = entry["reading"] or w.get("reading", "")
        w["word"] = learner.show(w.get("word", ""))  # new words: no unlearned kanji
    summary["stats"] = {"turns": sum(1 for m in msgs if m["role"] == "user"),
                        "corrections": len(corrections)}
    summary["targets"] = learner.target_report(session, db.session_observations(sid))
    db.end_session(sid, summary)
    return summary


# ------------------------------------------------------------------ backups

@app.get("/api/backup/status")
def backup_status():
    return {
        "freq": setting("backup_freq", "off"),
        "dir": backup.backup_dir(),
        "last": float(setting("backup_last", 0) or 0),
    }


@app.post("/api/backup/now")
def backup_run():
    try:
        return backup.backup_now()
    except Exception as e:
        return JSONResponse({"error": f"Backup failed: {e}"}, status_code=500)


@app.get("/api/backup/export")
def backup_export():
    tmp = backup.export_snapshot()
    name = f"kaiwa-backup-{date.today().isoformat()}.db"
    return FileResponse(tmp, filename=name, media_type="application/octet-stream",
                        background=BackgroundTask(os.remove, tmp))


@app.post("/api/backup/import")
async def backup_import(file: UploadFile = File(...)):
    err = backup.restore_from(await file.read())
    if err:
        return JSONResponse({"error": err}, status_code=400)
    return {"ok": True}


@app.post("/api/reset")
def reset_all_data():
    """Wipe everything back to a fresh install (debug / start over)."""
    try:
        backup.reset_all()
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"error": f"Reset failed: {e}"}, status_code=500)


@app.get("/api/dictionary")
def dictionary_search(q: str = ""):
    return {"results": dictionary.search(q)}


# -------------------------------------------------------------- voice (I/O)

@app.post("/api/stt")
def api_stt(audio: UploadFile = File(...), session_id: int | None = Form(None)):   # sync: Whisper blocks
    if not stt.available():
        return JSONResponse({"error": "speech model missing — run ./setup.sh"}, status_code=503)
    data = audio.file.read()
    # what the partner just said: short beginner replies are much easier to
    # recognize when Whisper knows what they're answering
    context = ""
    if session_id:
        context = next((m["text"] for m in reversed(db.get_messages(session_id))
                        if m["role"] == "assistant"), "")
    text = stt.transcribe(data, context=context)
    return {"text": text}


@app.post("/api/vad")
def api_vad(audio: UploadFile = File(...)):
    """End-of-turn detection for voice mode: where speech stops in the last few seconds."""
    return stt.speech_end(audio.file.read())


@app.get("/api/tts")
def api_tts(text: str, voice: str = "", speed: float = 1.0, intonation: float | None = None):
    v = voice or setting("voice") or tts.default_voice()
    into = intonation if intonation is not None else float(setting("intonation") or 1.0)
    try:
        wav = tts.synthesize(text, v, speed, into)
    except Exception:
        wav = tts.synthesize(text, "say:Kyoko", speed)  # engine fallback
    if not wav:
        return JSONResponse({"error": "tts failed"}, status_code=500)
    return Response(content=wav, media_type="audio/wav",
                    headers={"Cache-Control": "max-age=86400"})


# ---------------------------------------------------------------- dashboard

@app.get("/api/dashboard")
def dashboard():
    return db.dashboard()


@app.get("/api/speaking")
def speaking():
    """Which known words and grammar the learner has produced in conversation."""
    return learner.speaking(db.observation_stats(), db.observation_events())
