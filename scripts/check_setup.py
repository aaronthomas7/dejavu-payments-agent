"""Check that your keys work before you demo. Run:  python scripts/check_setup.py"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

OK, BAD, WARN = "[ OK ]", "[FAIL]", "[WARN]"


async def check_groq() -> bool:
    from app.llm import GroqJSONClient, LLMError, parse_keys

    keys = parse_keys(settings.groq_api_key)
    if not keys:
        print(f"{BAD} GROQ_API_KEY is empty. Create one at https://console.groq.com/keys and put it in .env")
        return False
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
    working = 0
    for i, key in enumerate(keys, 1):
        label = f"Groq key {i} of {len(keys)}" if len(keys) > 1 else "Groq"
        try:
            data, model = await GroqJSONClient(settings, api_keys=[key]).complete_json(
                "Reply with JSON.", 'Return {"ok": true}.', schema, "ping", 50)
            note = "" if model == settings.llm_model else f"  ({settings.llm_model} is out of quota on this key today)"
            print(f"{OK} {label} answered with {model}: {data}{note}")
            working += 1
        except LLMError as exc:
            print(f"{BAD} {label} failed: {exc}")
    if len(keys) == 1:
        print(f"{WARN} Tip: the free tier allows about 200K tokens a day per key. The replay plus the demo export use\n"
              f"       roughly 300K, so add a teammate's key after a comma:  GROQ_API_KEY=gsk_yours,gsk_theirs")
    return working == len(keys)


async def check_hindsight() -> bool:
    if not settings.hindsight_api_key and "localhost" not in settings.hindsight_base_url:
        print(f"{BAD} HINDSIGHT_API_KEY is empty. Create one at https://ui.hindsight.vectorize.io (add MEMHACK99 in Billing).")
        return False
    from app.memory import HindsightMemory

    mem = HindsightMemory(settings)
    try:
        info = await mem.setup()
        print(f"{OK} Hindsight bank '{info['bank_id']}' ready (directives: {', '.join(info['directives'])})")
        n = await mem.count()
        print(f"{OK} Bank currently holds {n} memories" + ("" if n else " - run: python scripts/replay.py --fresh"))
        return True
    except Exception as exc:
        print(f"{BAD} Hindsight failed: {exc}")
        return False
    finally:
        await mem.close()


async def main() -> None:
    print(f"Hindsight URL: {settings.hindsight_base_url} | bank: {settings.bank_id}")
    print(f"LLM: {settings.llm_model} (fallback {settings.llm_fallback_model}) via {settings.llm_base_url}\n")
    g = await check_groq()
    h = await check_hindsight()
    print()
    if g and h:
        print("All good. Next: python scripts/replay.py --fresh   then   uvicorn app.main:app --reload")
    else:
        print("Fix the failures above (keys go in the .env file next to README.md), then run this again.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
