"""Replay 6 weeks of payment exceptions and measure whether DejaVu actually learns.

For every historical case, in date order:
  1. memory OFF  - the same model, same prompt, no recall            -> baseline
  2. memory ON   - recall similar past cases + lessons from Hindsight -> DejaVu
  3. score both against the ground-truth root cause
  4. retain the analyst's resolution (plus whether DejaVu was right) into Hindsight

The bank starts EMPTY, so the only way the memory-ON score can rise above the
baseline is by learning from the cases it has already seen. Results are written
to data/replay_results.json (the UI's "Learning curve" tab) and docs/learning_curve.png.

    python scripts/replay.py --fresh          # wipe the bank first (required if it has memories)
    python scripts/replay.py --resume         # continue after an interruption (e.g. rate limits)
    python scripts/replay.py --limit 20       # quick run on the first 20 cases
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent import DejaVuAgent, build_reasoner  # noqa: E402
from app.config import settings  # noqa: E402
from app.memory import build_memory  # noqa: E402
from app.taxonomy import HUMAN_ONLY_CODES  # noqa: E402

RESULTS = settings.data_dir / "replay_results.json"
BASELINE_CACHE = settings.data_dir / "llm_cache.json"
CHART = ROOT / "docs" / "learning_curve.png"
VERIFY_MINUTES = 5  # assumed analyst time to verify a correct, high-confidence DejaVu diagnosis


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def summarize(rows: list[dict]) -> dict:
    def acc(sel, key):
        sel = list(sel)
        return round(sum(r[key] for r in sel) / len(sel), 3) if sel else None

    repeats = [r for r in rows if r["is_repeat"]]
    firsts = [r for r in rows if not r["is_repeat"]]
    cum_on, cum_off, on_ok, off_ok = [], [], 0, 0
    for i, r in enumerate(rows, 1):
        on_ok += r["on_correct"]
        off_ok += r["off_correct"]
        cum_on.append(round(on_ok / i, 3))
        cum_off.append(round(off_ok / i, 3))
    window = 10
    roll_on = [round(sum(x["on_correct"] for x in rows[max(0, i - window + 1): i + 1]) / len(rows[max(0, i - window + 1): i + 1]), 3)
               for i in range(len(rows))]
    roll_off = [round(sum(x["off_correct"] for x in rows[max(0, i - window + 1): i + 1]) / len(rows[max(0, i - window + 1): i + 1]), 3)
                for i in range(len(rows))]
    half = len(rows) // 2
    manual = sum(r.get("minutes_spent") or 0 for r in rows)
    with_dejavu = sum(
        VERIFY_MINUTES if (r["on_correct"] and r["on_confidence"] >= 0.8 and r["truth"] not in HUMAN_ONLY_CODES)
        else (r.get("minutes_spent") or 0)
        for r in rows)
    return {
        "cases": len(rows),
        "accuracy_on": acc(rows, "on_correct"), "accuracy_off": acc(rows, "off_correct"),
        "repeat_cases": len(repeats),
        "repeat_accuracy_on": acc(repeats, "on_correct"), "repeat_accuracy_off": acc(repeats, "off_correct"),
        "first_seen_accuracy_on": acc(firsts, "on_correct"), "first_seen_accuracy_off": acc(firsts, "off_correct"),
        "first_half_accuracy_on": acc(rows[:half], "on_correct"), "second_half_accuracy_on": acc(rows[half:], "on_correct"),
        "first_half_accuracy_off": acc(rows[:half], "off_correct"), "second_half_accuracy_off": acc(rows[half:], "off_correct"),
        "cumulative_on": cum_on, "cumulative_off": cum_off, "rolling_on": roll_on, "rolling_off": roll_off,
        "rolling_window": window,
        "minutes_manual": manual, "minutes_with_dejavu_estimate": with_dejavu,
        "high_confidence_correct": sum(1 for r in rows if r["on_correct"] and r["on_confidence"] >= 0.8),
        "high_confidence_wrong": sum(1 for r in rows if (not r["on_correct"]) and r["on_confidence"] >= 0.8),
    }


def make_chart(payload: dict) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed - skipping chart")
        return
    rows, s = payload["cases"], payload["summary"]
    x = list(range(1, len(rows) + 1))
    fig, ax = plt.subplots(figsize=(10, 5.2), dpi=160)
    ax.plot(x, [v * 100 for v in s["rolling_on"]], color="#0f766e", lw=2.6, label="DejaVu with Hindsight memory")
    ax.plot(x, [v * 100 for v in s["rolling_off"]], color="#9ca3af", lw=2.2, ls="--", label="Same model, no memory")
    ok_x = [i for i, r in enumerate(rows, 1) if r["on_correct"]]
    bad_x = [i for i, r in enumerate(rows, 1) if not r["on_correct"]]
    ax.scatter(ok_x, [104] * len(ok_x), s=16, color="#0f766e", zorder=3, label="DejaVu right on this case")
    ax.scatter(bad_x, [104] * len(bad_x), s=16, color="#dc2626", zorder=3, label="DejaVu wrong on this case")
    ax.set_ylim(0, 110)
    ax.set_xlim(1, len(rows))
    ax.set_xlabel("Exception number (chronological, 17 Aug to 25 Sep)")
    ax.set_ylabel(f"Root-cause accuracy, rolling {s['rolling_window']} cases (%)")
    ax.set_title(f"Memory ON {s['accuracy_on']:.0%} vs OFF {s['accuracy_off']:.0%} overall; on repeat patterns "
                 f"{(s['repeat_accuracy_on'] or 0):.0%} vs {(s['repeat_accuracy_off'] or 0):.0%}",
                 loc="left", fontsize=12, fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(loc="lower right", frameon=False, fontsize=9)
    if payload.get("offline"):
        ax.text(0.5, 0.45, "OFFLINE SIMULATION - NOT REAL RESULTS", transform=ax.transAxes, ha="center",
                fontsize=22, color="#dc2626", alpha=0.35, rotation=12, fontweight="bold")
    fig.tight_layout()
    CHART.parent.mkdir(exist_ok=True)
    fig.savefig(CHART)
    print(f"chart -> {CHART}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fresh", action="store_true", help="delete and recreate the memory bank before replaying")
    ap.add_argument("--resume", action="store_true", help="continue a previous interrupted run")
    ap.add_argument("--limit", type=int, default=0, help="only replay the first N cases")
    ap.add_argument("--no-baseline-cache", action="store_true", help="recompute memory-OFF answers even if cached")
    ap.add_argument("--pause", type=float, default=1.0, help="seconds to wait after each retain (lets consolidation run)")
    args = ap.parse_args()

    mem_mode, llm_mode = settings.resolved_modes()
    if mem_mode != "hindsight" or llm_mode != "groq":
        print("!! OFFLINE SIMULATION: memory=%s llm=%s. Add HINDSIGHT_API_KEY and GROQ_API_KEY to .env for real results.\n"
              % (mem_mode, llm_mode))
    memory = build_memory(settings, mem_mode)
    agent = DejaVuAgent(settings, memory, build_reasoner(settings, llm_mode), store=None)

    history = json.loads((settings.data_dir / "history.json").read_text(encoding="utf-8"))
    if args.limit:
        history = history[: args.limit]

    previous = load_json(RESULTS, {}) if args.resume else {}
    rows: list[dict] = previous.get("cases", []) if previous.get("bank_id") == memory.bank_id else []
    done_ids = {r["case_id"] for r in rows}

    try:
        await memory.setup()
    except Exception as exc:
        print(f"Could not reach/configure the memory bank: {exc}\nRun  python scripts/check_setup.py  for details.")
        await memory.close()
        raise SystemExit(1)
    if args.fresh:
        existing = await memory.count()
        if existing:
            print(f"Wiping memory bank '{memory.bank_id}' ({existing} memories) ...")
            try:
                await memory.reset()
            except Exception as exc:
                print(f"\nCould not wipe and re-create the bank: {exc}\n"
                      "Easy fix: put a new name in .env, e.g. HINDSIGHT_BANK_ID=dejavu-payments-desk-2, and run again.")
                await memory.close()
                raise SystemExit(1)
        else:
            print(f"Bank '{memory.bank_id}' is empty, starting fresh.")
        rows, done_ids = [], set()
    elif not rows and await memory.count() > 0:
        print(f"Bank '{memory.bank_id}' already has memories. Use --fresh for an honest learning curve "
              f"(or --resume to continue a previous run).")
        await memory.close()
        raise SystemExit(1)

    cache = {} if args.no_baseline_cache else load_json(BASELINE_CACHE, {})
    model_key = settings.llm_model if llm_mode == "groq" else "offline"
    started = time.time()
    todo = [c for c in history if c["case_id"] not in done_ids]
    print(f"Replaying {len(todo)} cases into bank '{memory.bank_id}' (memory={mem_mode}, llm={llm_mode}) ...\n")

    async def diagnose_reliably(case: dict, use_memory: bool):
        """Retry a degraded diagnosis (model or memory hiccup) a couple of times; None if it keeps failing."""
        for attempt in range(3):
            d = await agent.diagnose(case, use_memory=use_memory)
            if not d.degraded:
                return d
            print(f"  !! {case['case_id']} ({'memory ON' if use_memory else 'memory OFF'}): degraded -> "
                  f"{'; '.join(d.warnings)}. Retrying in {15 * (attempt + 1)}s ...")
            await asyncio.sleep(15 * (attempt + 1))
        return None

    for n, case in enumerate(todo, 1):
        gt = case["ground_truth"]
        key = f"{model_key}|{case['case_id']}"
        if key in cache:
            off = cache[key]
        else:
            d = await diagnose_reliably(case, use_memory=False)
            if d is not None:
                off = {"root_cause": d.root_cause, "confidence": d.confidence}
                cache[key] = off
                BASELINE_CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
        on = await diagnose_reliably(case, use_memory=True)
        if on is None or key not in cache:
            # Stop instead of skipping: cases must be learned in date order, or later ones would leak into earlier ones.
            print(f"\nStopping at {case['case_id']}: the model or memory kept failing (see the log above). "
                  "Fix it (python scripts/check_setup.py helps), then continue with:  python scripts/replay.py --resume")
            await memory.close()
            raise SystemExit(1)

        row = {
            "case_id": case["case_id"], "date": case["created_at"][:10], "bank": case["payment"]["creditor_bank"]["name"],
            "code": case.get("reason_code") or case["exception_type"], "pattern_id": gt["pattern_id"],
            "is_repeat": gt["is_repeat"], "truth": gt["root_cause"], "minutes_spent": gt.get("minutes_spent"),
            "on_root_cause": on.root_cause, "on_confidence": on.confidence, "on_correct": on.root_cause == gt["root_cause"],
            "on_evidence": [e.case_id for e in on.evidence if e.case_id], "on_recalled": len(on.recalled),
            "on_reasoning": on.reasoning, "on_latency_ms": on.latency_ms, "on_degraded": on.degraded,
            "off_root_cause": off["root_cause"], "off_confidence": off["confidence"],
            "off_correct": off["root_cause"] == gt["root_cause"],
        }
        await memory.retain_case(case, gt["root_cause"], gt["resolution_note"], on.root_cause, on.confidence)
        rows.append(row)

        mark = lambda ok: "OK " if ok else "-- "  # noqa: E731
        elapsed = time.time() - started
        eta = elapsed / n * (len(todo) - n)
        print(f"[{len(rows):>2}/{len(history)}] {case['case_id']} {row['bank'][:24]:<24} {row['code']:<18} "
              f"{'repeat' if row['is_repeat'] else 'new   '}  ON {mark(row['on_correct'])}{on.root_cause:<30} "
              f"OFF {mark(row['off_correct'])}{off['root_cause']:<30} eta {eta/60:4.1f}m")

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(), "bank_id": memory.bank_id,
            "mode": {"memory": mem_mode, "llm": llm_mode, "model": model_key}, "offline": mem_mode != "hindsight" or llm_mode != "groq",
            "summary": summarize(rows), "cases": rows,
        }
        RESULTS.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        if args.pause:
            await asyncio.sleep(args.pause)

    payload = load_json(RESULTS, {})
    s = payload.get("summary", {})
    if s:
        print("\n=== Summary ===")
        print(f"Overall accuracy       memory ON {s['accuracy_on']:.0%}   OFF {s['accuracy_off']:.0%}")
        if s["repeat_accuracy_on"] is not None:
            print(f"Repeat-pattern cases   memory ON {s['repeat_accuracy_on']:.0%}   OFF {s['repeat_accuracy_off']:.0%}   ({s['repeat_cases']} cases)")
        print(f"First half -> second half (ON): {s['first_half_accuracy_on']:.0%} -> {s['second_half_accuracy_on']:.0%}")
        print(f"Analyst minutes (estimate): {s['minutes_manual']} manual -> {s['minutes_with_dejavu_estimate']} with DejaVu")
        make_chart(payload)
    await memory.close()


if __name__ == "__main__":
    asyncio.run(main())
