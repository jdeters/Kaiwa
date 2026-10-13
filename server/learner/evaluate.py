"""Phase 1 evaluation: run scripted conversations between the constrained
partner and a simulated learner, and measure how often the partner's Japanese
stays inside the learner's knowledge.

The simulated learner is a cheap model playing a beginner; it isn't validated
(real learners say things outside their profile too).
"""
import json
import os
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import anthropic

from . import generate, grammar
from .profile import DATA, Profile

LEARNER_MODEL = "claude-haiku-5-5"
LEARNER_SCHEMA = {
    "type": "object",
    "properties": {"japanese": {"type": "string"}},
    "required": ["japanese"],
    "additionalProperties": False,
}


def eval_profiles(base: Profile) -> dict:
    return {
        "you": base,
        "lesson4": base.restricted(lesson=4, words_from={"Japanese for Busy People"}),
        "core": base.restricted(words_from={"Core 2000"}),
    }


def learner_reply(client, scenario: dict, lesson: int, transcript: list, usage: generate.Usage) -> str:
    convo = "\n".join(f"{'Partner' if r == 'ai' else 'You'}: {t}" for r, t in transcript)
    resp = client.messages.create(
        model=LEARNER_MODEL,
        max_tokens=1000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": LEARNER_SCHEMA}},
        messages=[{"role": "user", "content": (
            f"Role-play a beginner Japanese learner who has finished lesson {lesson} of Japanese for "
            f"Busy People. Scene: {scenario['setting']} You are: {scenario['user_role']}\n\n"
            f"Conversation so far:\n{convo}\n\n"
            "Write your next line: one short, simple polite sentence a beginner would say. About "
            "one time in four, make a typical beginner mistake (wrong particle or verb form). "
            "Sometimes just answer briefly. Standard Japanese spelling.")}],
    )
    usage.add(resp.usage)
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    try:
        return json.loads(text)["japanese"]
    except (json.JSONDecodeError, KeyError):
        return text.strip()


def run_conversation(client, profile: Profile, profile_name: str, scenario: dict,
                     model: str, turns: int, effort: str, concepts: dict, avoid=()) -> dict:
    from . import production
    partner = generate.Partner(profile, scenario, model=model, effort=effort,
                               client=client, concepts=concepts, avoid=set(avoid))
    learner_usage = generate.Usage()
    transcript, records = [], []
    error = None
    try:
        for k in range(turns):
            t = partner.ai_turn()
            transcript.append(("ai", t.japanese))
            records.append(asdict(t))
            if k < turns - 1:
                said = learner_reply(client, scenario, profile.snapshot["lesson"], transcript, learner_usage)
                transcript.append(("learner", said))
                partner.learner_says(said)
                for o in production.observe(said, profile):   # said it → partner may use it now
                    partner.avoid.discard(o.item)
                records[-1]["learner_reply"] = said
    except anthropic.APIError as e:
        error = f"{type(e).__name__}: {e}"
    return {
        "profile": profile_name, "scenario": scenario["id"], "model": model, "effort": effort,
        "turns": records, "error": error,
        "partner_usage": asdict(partner.usage), "partner_cost": partner.usage.cost(model),
        "learner_cost": learner_usage.cost(LEARNER_MODEL),
    }


def targets_said(profile: Profile, run: dict) -> dict:
    """Which of the run's targets the simulated learner produced on its own (or echoed)."""
    from . import production
    said = {"independent": set(), "echo": set()}
    for t in run["turns"]:   # each turn: the partner's line, then the learner's reply to it
        reply = t.get("learner_reply")
        if reply:
            for o in production.observe(reply, profile, prev_partner=t["japanese"]):
                if (o.kind, o.item) in run["targets"] and o.outcome in said:
                    said[o.outcome].add((o.kind, o.item))
    return {k: sorted(v) for k, v in said.items()}


def summarize(runs: list) -> list:
    rows = []
    keys = sorted({(r["model"], r["profile"]) for r in runs})
    for model, prof in keys + [(m, "ALL") for m in sorted({r["model"] for r in runs})]:
        sel = [r for r in runs if r["model"] == model and (prof == "ALL" or r["profile"] == prof)]
        turns = [t for r in sel for t in r["turns"]]
        if not turns:
            continue
        first_ok = sum(not t["attempts"][0]["violations"] for t in turns)
        final_ok = sum(t["ok"] for t in turns)
        secs = [t["seconds"] for t in turns]
        draft_secs = [a["seconds"] for t in turns for a in t["attempts"]]
        rows.append({
            "model": model, "profile": prof, "turns": len(turns),
            "first_try_ok": round(first_ok / len(turns), 3),
            "final_ok": round(final_ok / len(turns), 3),
            "avg_attempts": round(sum(len(t["attempts"]) for t in turns) / len(turns), 2),
            "median_s": round(statistics.median(secs), 2),
            "p90_s": round(sorted(secs)[int(0.9 * (len(secs) - 1))], 2),
            "median_draft_s": round(statistics.median(draft_secs), 2),
            "partner_cost": round(sum(r["partner_cost"] for r in sel), 4),
            "cost_per_turn": round(sum(r["partner_cost"] for r in sel) / len(turns), 5),
            "errors": sum(bool(r["error"]) for r in sel),
            "salvaged": sum(bool(t.get("salvaged")) for t in turns),
            "targets_said": round(sum(len(r.get("targets_said", {}).get("independent", [])) for r in sel)
                                  / max(1, sum(len(r.get("targets", [])) for r in sel)), 3),
        })
    return rows


def violation_counts(runs: list) -> dict:
    """Which violations survive repair (final turns) and which show up in first drafts."""
    first, final = {}, {}
    for r in runs:
        for t in r["turns"]:
            for v in t["attempts"][0]["violations"]:
                key = v.split("」")[0] + "」" if v.startswith("[vocab]") else v.split(" uses ")[-1]
                first[key] = first.get(key, 0) + 1
            if not t["ok"]:
                best = min(t["attempts"], key=lambda a: len(a["violations"]))
                for v in best["violations"]:
                    final[v] = final.get(v, 0) + 1
    top = lambda d: sorted(d.items(), key=lambda kv: -kv[1])[:30]
    return {"first_draft": top(first), "unrepaired": top(final)}


def transcript_md(runs: list) -> str:
    out = ["# Phase 1 transcripts\n"]
    for r in sorted(runs, key=lambda r: (r["model"], r["profile"], r["scenario"])):
        out.append(f"\n## {r['model']} · {r['profile']} · {r['scenario']}\n")
        if r["error"]:
            out.append(f"**Error:** {r['error']}\n")
        for t in r["turns"]:
            mark = "" if t["ok"] else " ❌"
            out.append(f"- **AI**{mark}: {t['display']}  \n  {t['japanese']}  \n  _{t['english']}_ "
                       f"({t['seconds']}s, {len(t['attempts'])} draft{'s' if len(t['attempts']) > 1 else ''})")
            if t.get("reading_fixes"):
                out.append("  - readings fixed: " + "; ".join(f"{s} {a}→{b}" for s, a, b in t["reading_fixes"]))
            for a in t["attempts"][:-1] if t["ok"] else t["attempts"]:
                if a["violations"]:
                    out.append(f"  - rejected: {a['japanese']} — {'; '.join(a['violations'])}")
            if t.get("learner_reply"):
                out.append(f"- **Learner**: {t['learner_reply']}")
    return "\n".join(out) + "\n"


def run(models: list, profile_names: list, scenario_ids: list, turns: int = 6,
        effort: str = "low", workers: int = 6, out_dir: str | None = None,
        targets: bool = False) -> str:
    """targets: give each conversation practice targets, as the app does (picked
    from the real learner profile, so only meaningful with the "you" profile)."""
    client = anthropic.Anthropic()
    base = Profile.load()
    profiles = eval_profiles(base)
    scenarios = generate.load_scenarios()
    concepts = grammar.load_concepts()
    jobs = [(m, p, s) for m in models for p in profile_names for s in scenario_ids]
    out_dir = out_dir or os.path.join(DATA, "phase1", time.strftime("%Y%m%d-%H%M%S")
                                      + ("-targets" if targets else "") + f"-{os.getpid()}")
    os.makedirs(out_dir, exist_ok=True)

    def job(args):
        m, p, s = args
        scen = scenarios[s]
        # the same targets in both modes (seeded by scenario): with --targets the
        # partner steers toward them; without, they measure the baseline rate
        picked = []
        if p == "you":
            from . import service
            picked = service.choose_targets([], random.Random(s))
            if targets:
                scen = {**scen, "notes": [service._targets_note(picked)]}
        avoid = [t["item"] for t in picked if t["kind"] == "word"] if targets else []
        r = run_conversation(client, profiles[p], p, scen, m, turns, effort, concepts, avoid)
        r["targets"] = [(t["kind"], t["item"]) for t in picked]
        r["targets_said"] = targets_said(profiles[p], r)
        ok = sum(t["ok"] for t in r["turns"])
        print(f"  {m:18} {p:8} {s:15} {ok}/{len(r['turns'])} ok  ${r['partner_cost']:.3f}"
              + (f"  ERROR {r['error']}" if r["error"] else ""), flush=True)
        return r

    print(f"running {len(jobs)} conversations × {turns} turns → {out_dir}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        runs = list(pool.map(job, jobs))

    summary = {"summary": summarize(runs), "violations": violation_counts(runs),
               "learner_cost": round(sum(r["learner_cost"] for r in runs), 4)}
    with open(os.path.join(out_dir, "runs.json"), "w", encoding="utf-8") as f:
        json.dump(runs, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "transcripts.md"), "w", encoding="utf-8") as f:
        f.write(transcript_md(runs))
    return out_dir
