import httpx
import pytest

from lmstudio_mcp.client import Client
from lmstudio_mcp.config import Settings
from lmstudio_mcp.server import create_server
from lmstudio_mcp.storage import atomic_json


@pytest.mark.parametrize('broken_profile', [False, True])
async def test_stateless_http_repeated_sessions_do_not_reuse_closed_clients(tmp_path, monkeypatch, broken_profile):
    class TestClient(Client):
        def __init__(self, settings):
            super().__init__(settings,
                transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"models": []})),
                remote_transport=httpx.MockTransport(lambda r: httpx.Response(200, text="LM Studio 0.4.24")))

        async def cli(self, args, timeout=30):
            return {"stdout": "test-runtime", "stderr": "", "exit_code": 0}

    monkeypatch.setattr("lmstudio_mcp.server.Client", TestClient)
    if broken_profile:
        monkeypatch.delenv('LM_REMOTE_MISSING_TOKEN', raising=False)
        atomic_json(tmp_path / 'connections.json', {'active': 'broken', 'profiles': {
            'broken': {'url': 'http://192.168.1.10:1234', 'token_env': 'LM_REMOTE_MISSING_TOKEN'}}})
    server = create_server(Settings(state_dir=tmp_path, mcp_config_path=tmp_path / "mcp.json"))
    app = server.streamable_http_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8765", headers={"Accept": "application/json, text/event-stream"}) as client,
    ):
        response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}}})
        assert response.status_code == 200
        for index in range(2, 5):
            response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": index, "method": "tools/call",
                "params": {"name": "lm_connections" if broken_profile else "lm_models",
                           "arguments": {"action": "list"} if broken_profile else {}}})
            assert response.status_code == 200, response.text
            result = response.json()["result"]
            assert not result.get("isError"), result
            assert result["structuredContent"]["ok"]
