"""Phase 1 command line.

  python -m server.learner build                 import from Anki → data/learner_profile.json
  python -m server.learner check "文"            validate a sentence against the profile
  python -m server.learner chat cafe             text conversation with the constrained partner
  python -m server.learner eval                  run the evaluation (see --help)

Reads ANTHROPIC_API_KEY from the environment or ./.env.
"""
import argparse
import json
import os
import sys

from .. import llm


def cmd_build(args):
    from . import anki, profile
    try:
        snap = profile.build(profile.load_config())
    except anki.AnkiError as e:
        sys.exit(f"error: {e}")
    profile.save(snap)
    words = [w for w in snap["words"] if not w["lemma"].startswith("reading:")]
    print(f"lesson {snap['lesson']} · {len(snap['grammar'])} grammar concepts · "
          f"{len(words)} words · {len(snap['phrases'])} phrases · notes per deck {snap['stats']}")
    print(f"→ {profile.SNAPSHOT_PATH}")


def cmd_check(args):
    from . import display, validator
    from .profile import Profile
    p = Profile.load()
    for text in args.text:
        r = validator.validate(text, p)
        print(("✓ " if r.ok else "✗ ") + text + "   →  " + display.render(text, p).display)
        for v in r.violations:
            print(f"    {v}")
        if r.proper_nouns:
            print(f"    names: {', '.join(r.proper_nouns)}")


def cmd_chat(args):
    from . import generate
    from .profile import Profile
    p = Profile.load()
    scenarios = generate.load_scenarios()
    if args.scenario not in scenarios:
        sys.exit(f"unknown scenario; choose from: {', '.join(scenarios)}")
    partner = generate.Partner(p, scenarios[args.scenario], model=args.model, effort=args.effort)
    print(f"{scenarios[args.scenario]['title']} — type Japanese, empty line to quit\n")
    while True:
        t = partner.ai_turn()
        print(f"AI: {t.display}" + ("" if t.ok else "   [still has violations]"))
        if args.kanji:
            print(f"    {t.japanese}")
        if args.english:
            print(f"    {t.english}")
        if args.verbose:
            for a in t.attempts:
                if a["violations"]:
                    print(f"    rejected: {a['japanese']} — {'; '.join(a['violations'])}")
            print(f"    ({t.seconds}s, {len(t.attempts)} draft(s))")
        said = input("You: ").strip()
        if not said:
            break
        partner.learner_says(said)
    print(f"\ncost ≈ ${partner.usage.cost(args.model):.4f}")


def cmd_eval(args):
    from . import evaluate, generate
    scenarios = list(generate.load_scenarios()) if args.scenarios == "all" else args.scenarios.split(",")
    out = evaluate.run(args.models.split(","), args.profiles.split(","), scenarios,
                       turns=args.turns, effort=args.effort, workers=args.workers,
                       targets=args.targets)
    summary = json.load(open(os.path.join(out, "summary.json"), encoding="utf-8"))
    print(f"\n{'model':18} {'profile':8} {'turns':>5} {'1st ok':>7} {'final':>6} {'tries':>5} "
          f"{'med s':>6} {'p90 s':>6} {'$/turn':>8} {'salvaged':>8} {'targets said':>12}")
    for r in summary["summary"]:
        print(f"{r['model']:18} {r['profile']:8} {r['turns']:5} {r['first_try_ok']:7.1%} "
              f"{r['final_ok']:6.1%} {r['avg_attempts']:5} {r['median_s']:6} {r['p90_s']:6} "
              f"{r['cost_per_turn']:8.5f} {r.get('salvaged', 0):8} {r.get('targets_said', 0):12.1%}")
    print(f"\nsimulated learner cost ${summary['learner_cost']}\n→ {out}")


def main():
    llm.load_env()
    ap = argparse.ArgumentParser(prog="python -m server.learner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build").set_defaults(fn=cmd_build)
    c = sub.add_parser("check")
    c.add_argument("text", nargs="+")
    c.set_defaults(fn=cmd_check)
    ch = sub.add_parser("chat")
    ch.add_argument("scenario")
    ch.add_argument("--model", default="claude-opus-5-5")
    ch.add_argument("--effort", default="low")
    ch.add_argument("--english", action="store_true", help="show translations")
    ch.add_argument("--kanji", action="store_true", help="also show the full-kanji original")
    ch.add_argument("-v", "--verbose", action="store_true", help="show rejected drafts and timing")
    ch.set_defaults(fn=cmd_chat)
    e = sub.add_parser("eval")
    e.add_argument("--models", default="claude-opus-5-5,claude-sonnet-5-5,claude-haiku-5-5")
    e.add_argument("--profiles", default="you,lesson4,core")
    e.add_argument("--scenarios", default="all")
    e.add_argument("--turns", type=int, default=6)
    e.add_argument("--effort", default="low")
    e.add_argument("--workers", type=int, default=6)
    e.add_argument("--targets", action="store_true", help="give conversations practice targets")
    e.set_defaults(fn=cmd_eval)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
