"""HindsightMemory against a fake client: empty-bank quirks and retries (no network)."""
import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("hindsight_client")
from hindsight_client_api.exceptions import ApiException  # noqa: E402

import app.memory as memory_mod  # noqa: E402
from app.config import Settings  # noqa: E402
from app.memory import HindsightMemory, MemoryUnavailable  # noqa: E402

NO_MANIFEST = '{"detail":"[13] wal: namespace tenant-x/bank has no manifest"}'


def _err(status, body=""):
    return ApiException(status=status, reason="err", body=body)


class FakeClient:
    def __init__(self):
        self.calls = []
        self.fail = {}  # method -> list of exceptions to raise first

    def _maybe_fail(self, name):
        self.calls.append(name)
        queue = self.fail.get(name) or []
        if queue:
            raise queue.pop(0)

    async def acreate_bank(self, **kw): self._maybe_fail("acreate_bank")
    async def aupdate_bank_config(self, *a, **kw): self._maybe_fail("aupdate_bank_config")
    async def alist_directives(self, *a, **kw):
        self._maybe_fail("alist_directives")
        return SimpleNamespace(items=[])
    async def acreate_directive(self, *a, **kw): self._maybe_fail("acreate_directive")
    async def aget_mental_model(self, *a, **kw):
        self._maybe_fail("aget_mental_model")
        raise _err(404)
    async def acreate_mental_model(self, *a, **kw): self._maybe_fail("acreate_mental_model")
    async def alist_memories(self, *a, **kw):
        self._maybe_fail("alist_memories")
        return SimpleNamespace(total=0, items=[])
    async def aretain(self, *a, **kw): self._maybe_fail("aretain")
    async def arecall(self, *a, **kw):
        self._maybe_fail("arecall")
        return SimpleNamespace(results=[])
    async def aclose(self): pass


@pytest.fixture
def mem(monkeypatch):
    async def no_sleep(_):
        return None
    monkeypatch.setattr(memory_mod.asyncio, "sleep", no_sleep)
    m = HindsightMemory(Settings())
    m.client = FakeClient()
    return m


def test_setup_survives_playbook_refusal_and_retries_after_retain(mem, live):
    mem.client.fail["acreate_mental_model"] = [_err(500, NO_MANIFEST)]
    asyncio.run(mem.setup())
    assert mem.ready and mem.playbook_pending
    asyncio.run(mem.retain_case(live[0], "CLIENT_TYPO_IN_DETAILS", "fixed"))
    assert not mem.playbook_pending  # created once the bank had data
    assert mem.client.calls.count("acreate_mental_model") == 2


def test_empty_bank_means_no_memories_not_an_outage(mem, live):
    mem.client.fail["arecall"] = [_err(500, NO_MANIFEST), _err(500, NO_MANIFEST)]
    assert asyncio.run(mem.recall_for_case(live[0])) == []
    mem.client.fail["alist_memories"] = [_err(500, NO_MANIFEST)]
    assert asyncio.run(mem.count()) == 0


def test_real_outage_still_raises(mem, live):
    mem.client.fail["arecall"] = [_err(503, "down"), _err(503, "down")]
    with pytest.raises(MemoryUnavailable):
        asyncio.run(mem.recall_for_case(live[0]))


def test_transient_retain_error_is_retried(mem, live):
    mem.client.fail["aretain"] = [_err(502, "bad gateway")]
    asyncio.run(mem.retain_case(live[0], "CLIENT_TYPO_IN_DETAILS", "fixed"))
    assert mem.client.calls.count("aretain") == 2
