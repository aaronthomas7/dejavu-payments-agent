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
    if not settings.groq_api_key:
        print(f"{BAD} GROQ_API_KEY is empty. Create one at https://console.groq.com/keys and put it in .env")
        return False
    from app.llm import GroqJSONClient, LLMError

    client = GroqJSONClient(settings)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
    try:
        data, model = await client.complete_json("Reply with JSON.", 'Return {"ok": true}.', schema, "ping", 50)
        print(f"{OK} Groq answered with {model}: {data}")
        return True
    except LLMError as exc:
        print(f"{BAD} Groq call failed: {exc}")
        return False


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
