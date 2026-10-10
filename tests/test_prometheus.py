import httpx
import pytest

from governor.prometheus import Prometheus


async def test_incomplete_prometheus_evidence_is_rejected(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            return httpx.Response(
                200,
                json={"status": "success", "warnings": ["partial data"], "data": {"result": []}},
                request=httpx.Request("GET", "http://example.invalid"),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())
    with pytest.raises(RuntimeError, match="incomplete"):
        await Prometheus("http://example.invalid").query("up")


async def test_unknown_pool_cannot_construct_promql():
    with pytest.raises(ValueError):
        await Prometheus("http://example.invalid").snapshot('x"} or up')
