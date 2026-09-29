"""Record a real run of DejaVu into a static, clickable demo (for GitHub Pages).

Run this AFTER scripts/replay.py, with your real keys in .env:

    python scripts/export_demo.py

It asks the live agent for everything the UI can show: memory ON/OFF diagnoses for every
open case, the lessons, the playbook, the suggested "Ask DejaVu" questions and the
pre-flight examples. It saves the answers as JSON next to a copy of the web UI in
docs/demo/. Nothing is written to the memory bank.

GitHub Pages then serves docs/ at https://<user>.github.io/<repo>/ with no keys and no server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent import DejaVuAgent, build_reasoner  # noqa: E402
from app.config import settings  # noqa: E402
from app.memory import build_memory  # noqa: E402
from app.models import PrecheckRequest  # noqa: E402
from app.taxonomy import ROOT_CAUSES  # noqa: E402

STATIC = ROOT / "app" / "static"
DEFAULT_REPO = "https://github.com/aaronthomas7/dejavu-payments-agent"


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


def public_case(case: dict, status: str) -> dict:
    out = {k: v for k, v in case.items() if k != "ground_truth"}
    out["status"] = status
    out["source"] = "history" if status == "resolved" else "live"
    gt = case.get("ground_truth") or {}
    out["resolution"] = ({"root_cause": gt.get("root_cause"), "note": gt.get("resolution_note"), "historical": True}
                         if status == "resolved" else None)
    return out


async def diagnose_reliably(agent, case, use_memory):
    for attempt in range(3):
        d = await agent.diagnose(case, use_memory=use_memory)
        if not d.degraded:
            return d
        print(f"  !! {case['case_id']} degraded ({'; '.join(d.warnings)}), retrying in {15 * (attempt + 1)}s")
        await asyncio.sleep(15 * (attempt + 1))
    raise SystemExit(f"Could not get a clean diagnosis for {case['case_id']} - check your keys/limits and run again.")


def playbook_ready(pb: dict) -> bool:
    text = (pb.get("content") or "").strip()
    return len(text) > 200 and not text.lower().startswith("generating")


async def record_playbook(memory, offline: bool) -> dict:
    """Fetch the playbook; if Hindsight is still writing it, ask for a refresh and wait (up to ~4 min)."""
    playbook = await memory.playbook()
    if offline or playbook_ready(playbook):
        return playbook
    try:
        await memory.refresh_playbook()
    except Exception as exc:  # already refreshing is fine
        print(f"  (refresh request: {exc})")
    for i in range(24):
        await asyncio.sleep(10)
        playbook = await memory.playbook()
        if playbook_ready(playbook):
            break
        print(f"  waiting for Hindsight to finish the playbook ... {(i + 1) * 10}s")
    return playbook


def build_site(out: Path) -> None:
    """Copy the UI next to the recorded data, switched into static mode with relative paths."""
    out.mkdir(parents=True, exist_ok=True)
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/static/', 'href="').replace('src="/static/', 'src="')
    html = html.replace('<script src="app.js"></script>', '<script>window.DEJAVU_STATIC = true;</script>\n  <script src="app.js"></script>')
    assert "DEJAVU_STATIC" in html, "could not inject static flag"
    (out / "index.html").write_text(html, encoding="utf-8")
    for name in ("app.js", "styles.css", "demo_inputs.json"):
        shutil.copy2(STATIC / name, out / name)
    shutil.copytree(STATIC / "vendor", out / "vendor", dirs_exist_ok=True)
    docs = out.parent
    (docs / ".nojekyll").write_text("", encoding="utf-8")
    if not (docs / "index.html").exists() or "demo/" in (docs / "index.html").read_text(encoding="utf-8"):
        (docs / "index.html").write_text(
            '<!doctype html><meta charset="utf-8"><title>DejaVu</title>'
            '<meta http-equiv="refresh" content="0; url=demo/"><a href="demo/">Open the DejaVu demo</a>\n', encoding="utf-8")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "docs" / "demo"), help="output folder (default docs/demo)")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="repo link shown in the demo banner")
    ap.add_argument("--allow-offline", action="store_true", help="testing only: export using the offline stand-ins")
    ap.add_argument("--only", choices=["playbook"], help="re-record just the playbook and lessons (no model calls)")
    args = ap.parse_args()

    mem_mode, llm_mode = settings.resolved_modes()
    offline = mem_mode != "hindsight" or llm_mode != "groq"
    if offline and not args.allow_offline:
        raise SystemExit("Add HINDSIGHT_API_KEY and GROQ_API_KEY to .env first - the demo must show real answers.")
    out = Path(args.out)
    data = out / "data"

    memory = build_memory(settings, mem_mode)
    await memory.setup()
    if args.only == "playbook":
        if not (data / "status.json").exists():
            raise SystemExit("No recorded demo yet. Run  python scripts/export_demo.py  first.")
        print("Re-recording the playbook and lessons ...")
        playbook = await record_playbook(memory, offline)
        write(data / "playbook.json", playbook)
        write(data / "lessons.json", {"items": await memory.lessons(limit=40)})
        status = json.loads((data / "status.json").read_text(encoding="utf-8"))
        status["memory_counts"] = await memory.stats()
        write(data / "status.json", status)
        await memory.close()
        print(f"Done: playbook {'ready' if playbook_ready(playbook) else 'STILL NOT READY - run this again in a few minutes'}"
              f" ({len((playbook.get('content') or '').strip())} characters).")
        return
    if data.exists():
        shutil.rmtree(data)
    agent = DejaVuAgent(settings, memory, build_reasoner(settings, llm_mode), store=None)
    if await memory.count() == 0:
        raise SystemExit("The memory bank is empty. Run  python scripts/replay.py --fresh  first.")

    history = json.loads((settings.data_dir / "history.json").read_text(encoding="utf-8"))
    live = json.loads((settings.data_dir / "live.json").read_text(encoding="utf-8"))
    inputs = json.loads((STATIC / "demo_inputs.json").read_text(encoding="utf-8"))
    started = time.time()

    print(f"Recording {len(live)} open cases (memory OFF and ON) ...")
    for i, case in enumerate(live, 1):
        for use_memory in (False, True):
            d = await diagnose_reliably(agent, case, use_memory)
            write(data / "diagnose" / f"{case['case_id']}.{'on' if use_memory else 'off'}.json", d.model_dump())
        print(f"  [{i}/{len(live)}] {case['case_id']} {case['payment']['creditor_bank']['name']}")

    print("Recording lessons and playbook ...")
    write(data / "lessons.json", {"items": await memory.lessons(limit=40)})
    write(data / "playbook.json", await record_playbook(memory, offline))

    print("Recording Ask DejaVu answers ...")
    for i, q in enumerate(inputs["suggestions"]):
        write(data / "ask" / f"{i}.json", (await memory.ask(q)).model_dump())

    print("Recording pre-flight checks ...")
    for i, preset in enumerate(inputs["presets"]):
        fields = {k: v for k, v in preset["v"].items() if v not in ("", None)}
        write(data / "precheck" / f"{i}.json", (await memory.precheck(PrecheckRequest(**fields))).model_dump())

    replay_path = settings.data_dir / "replay_results.json"
    write(data / "replay.json", {"available": True, **json.loads(replay_path.read_text(encoding="utf-8"))} if replay_path.exists()
          else {"available": False})
    write(data / "taxonomy.json", [{"code": rc.code, "label": rc.label, "description": rc.description,
                                     "default_fix": rc.default_fix, "human_only": rc.human_only} for rc in ROOT_CAUSES])
    write(data / "entities.json", json.loads((settings.data_dir / "entities.json").read_text(encoding="utf-8")))
    write(data / "cases_open.json", [public_case(c, "open") for c in live])
    write(data / "cases_resolved.json", [public_case(c, "resolved") for c in reversed(history)])
    write(data / "status.json", {
        "memory_backend": mem_mode, "llm_backend": llm_mode, "offline": offline, "bank_id": memory.bank_id,
        "model": settings.llm_model if llm_mode == "groq" else "offline-heuristic", "setup_error": None,
        "memory_counts": await memory.stats(), "recorded_at": datetime.now(timezone.utc).isoformat(),
        "repo_url": args.repo,
    })
    build_site(out)
    await memory.close()
    print(f"\nDone in {(time.time() - started) / 60:.1f} min -> {out}")
    print("Commit the docs/ folder and push; GitHub Pages serves it at https://<user>.github.io/<repo>/")


if __name__ == "__main__":
    asyncio.run(main())
