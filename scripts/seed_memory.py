"""Load the 6 weeks of resolved history into a memory bank WITHOUT evaluating.

Use this when you need a populated bank quickly (e.g. a new bank for a live demo)
and already have replay results. For the learning curve use scripts/replay.py.

    python scripts/seed_memory.py            # into HINDSIGHT_BANK_ID
    python scripts/seed_memory.py --fresh    # wipe the bank first
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.memory import build_memory  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args()
    mode, _ = settings.resolved_modes()
    memory = build_memory(settings, mode)
    await memory.setup()
    if args.fresh:
        await memory.reset()
    history = json.loads((settings.data_dir / "history.json").read_text())
    for i, case in enumerate(history, 1):
        gt = case["ground_truth"]
        await memory.retain_case(case, gt["root_cause"], gt["resolution_note"])
        print(f"[{i:>2}/{len(history)}] retained {case['case_id']} ({gt['root_cause']})")
    print(f"Done. Bank '{memory.bank_id}' now holds {await memory.count()} memories. "
          "Hindsight keeps consolidating lessons in the background for a few minutes.")
    await memory.close()


if __name__ == "__main__":
    asyncio.run(main())
