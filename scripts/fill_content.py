"""Copy the real replay numbers into the README table and the content drafts.

    python scripts/fill_content.py

Reads data/replay_results.json (produced by scripts/replay.py with real API keys),
fills {{PLACEHOLDERS}} in content/*.md and writes the results to content/final/.
Refuses to use offline-simulation results.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "data" / "replay_results.json"


def pct(v) -> str:
    return "n/a" if v is None else f"{round(v * 100)}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--github", default="https://github.com/aaronthomas7/dejavu-payments-agent")
    ap.add_argument("--allow-offline", action="store_true")
    args = ap.parse_args()

    if not RESULTS.exists():
        sys.exit("No data/replay_results.json yet. Run: python scripts/replay.py --fresh")
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    if payload.get("offline") and not args.allow_offline:
        sys.exit("These replay results came from the OFFLINE stand-ins. Re-run the replay with real API keys.")
    s = payload["summary"]
    values = {
        "ACC_ON": pct(s["accuracy_on"]), "ACC_OFF": pct(s["accuracy_off"]),
        "REPEAT_ON": pct(s["repeat_accuracy_on"]), "REPEAT_OFF": pct(s["repeat_accuracy_off"]),
        "REPEAT_CASES": str(s["repeat_cases"]), "CASES": str(s["cases"]),
        "FIRST_HALF_ON": pct(s["first_half_accuracy_on"]), "SECOND_HALF_ON": pct(s["second_half_accuracy_on"]),
        "HOURS_MANUAL": str(round(s["minutes_manual"] / 60)), "HOURS_DEJAVU": str(round(s["minutes_with_dejavu_estimate"] / 60)),
        "HIGH_CONF_WRONG": str(s["high_confidence_wrong"]), "MODEL": payload["mode"]["model"],
        "GITHUB_URL": args.github,
    }

    out_dir = ROOT / "content" / "final"
    out_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted((ROOT / "content").glob("*.md")):
        text = src.read_text(encoding="utf-8")
        for k, v in values.items():
            text = text.replace("{{" + k + "}}", v)
        left = sorted(set(re.findall(r"\{\{([A-Z_]+)\}\}", text)))
        (out_dir / src.name).write_text(text, encoding="utf-8")
        print(f"wrote content/final/{src.name}" + (f"  (still to fill by hand: {', '.join(left)})" if left else ""))

    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    text = re.sub(r"\| Root-cause accuracy \(all[^)]*\) \|.*\|",
                  f"| Root-cause accuracy (all {values['CASES']} cases) | **{values['ACC_ON']}** | {values['ACC_OFF']} |", text)
    text = re.sub(r"\| Accuracy on repeat patterns[^|]*\|.*\|",
                  f"| Accuracy on repeat patterns ({values['REPEAT_CASES']} cases) | **{values['REPEAT_ON']}** | {values['REPEAT_OFF']} |", text)
    readme.write_text(text, encoding="utf-8")
    print("updated README.md results table")
    print(json.dumps(values, indent=1))


if __name__ == "__main__":
    main()
