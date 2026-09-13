import json
import plistlib

import httpx
import pytest

from lmstudio_mcp.client import Client, ConnectorError, latest_version
from lmstudio_mcp.config import AdvancedLoadOptions, Settings
from lmstudio_mcp.service import Service

MODEL = {"key": "test/model", "type": "llm", "format": "mlx", "max_context_length": 8192,
         "loaded_instances": [], "capabilities": {"vision": True,
         "reasoning": {"allowed_options": ["off", "on"]}}}


@pytest.fixture
async def rig(tmp_path):
    calls = []
    state = {"models": [json.loads(json.dumps(MODEL))], "fail": None, "schema": False,
             "remote_fail": False, "unload_stuck": False, "ignored_config": False}

    def handler(request):
        calls.append(request)
        if state["fail"]:
            return httpx.Response(state["fail"], json={"error": "SECRET-TOKEN prompt-content"})
        path = request.url.path
        payload = json.loads(request.content) if request.content else {}
        if path == "/api/v1/models":
            return httpx.Response(200, json={"wrong": []} if state["schema"] else {"models": state["models"]})
        if path == "/api/v1/models/load":
            config = {} if state["ignored_config"] else {"context_length": payload.get("context_length", 4096)}
            state["models"][0]["loaded_instances"] = [{"id": "instance-1", "config": config}]
            return httpx.Response(200, json={"instance_id": "instance-1", "load_config": config})
        if path == "/api/v1/models/unload":
            if not state["unload_stuck"]:
                state["models"][0]["loaded_instances"] = []
            return httpx.Response(200, json={"instance_id": payload["instance_id"]})
        if path == "/api/v1/chat":
            return httpx.Response(200, json={"output": [{"type": "message", "content": "ok"}], "response_id": "resp_test"})
        if path == "/api/v1/models/download":
            return httpx.Response(200, json={"job_id": "job_x", "status": "downloading"})
        if path == "/api/v1/models/download/status/job_x":
            return httpx.Response(200, json={"job_id": "job_x", "status": "completed"})
        if path == "/v1/embeddings":
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
        return httpx.Response(404)

    def remote(request):
        calls.append(request)
        if state["remote_fail"]:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, text="<h2>LM Studio 0.4.24</h2><h2>LM Studio 0.4.9</h2>")

    plist = tmp_path / "Info.plist"
    plist.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "0.4.24+1"}))
    client = Client(Settings(state_dir=tmp_path, app_plist=plist, api_token="SECRET-TOKEN",
                            mcp_config_path=tmp_path / "mcp.json", rag_roots=[tmp_path / "documents"]),
                    httpx.MockTransport(handler), httpx.MockTransport(remote))
    async def fake_cli(args, timeout=30):
        return {"stdout": "test-runtime@1.0", "stderr": "", "exit_code": 0}
    client.cli = fake_cli
    yield client, Service(client), state, calls
    await client.close()


def test_versions_are_semantic_and_ignore_scripts():
    assert latest_version('<script>LM Studio 9.0.0</script>LM Studio 0.4.9 LM Studio 0.4.24') == '0.4.24'


@pytest.mark.parametrize("url", ["https://example.com", "http://127.0.0.1@evil.test", "http://127.0.0.1:1234/foo"])
def test_local_origin_only(url):
    with pytest.raises(ValueError):
        Settings(base_url=url)


async def test_per_call_preflight_and_cached_upstream(rig):
    _, service, _, calls = rig
    assert (await service.run("models"))["ok"]
    assert (await service.run("models"))["ok"]
    assert sum(r.url.path == "/api/v1/models" for r in calls) == 2
    assert sum(r.url.host == "lmstudio.ai" for r in calls) == 4
    for request in calls:
        if request.url.host == "lmstudio.ai":
            assert "authorization" not in request.headers
        else:
            assert request.headers["authorization"] == "Bearer SECRET-TOKEN"


@pytest.mark.parametrize("code", [401, 403, 500])
async def test_http_failure_blocks_mutation_and_redacts(rig, code):
    _, service, state, calls = rig
    state["fail"] = code
    result = await service.run("load", model="test/model")
    assert not result["ok"]
    assert "SECRET-TOKEN" not in json.dumps(result)
    assert "prompt-content" not in json.dumps(result)
    assert all(r.method == "GET" for r in calls)


async def test_unknown_schema_blocks_mutation(rig):
    _, service, state, calls = rig
    state["schema"] = True
    result = await service.run("load", model="test/model")
    assert not result["ok"]
    assert all(r.method == "GET" for r in calls)


async def test_offline_update_not_claimed_current(rig):
    _, service, state, _ = rig
    state["remote_fail"] = True
    result = await service.run("models")
    assert result["ok"]
    assert result["diagnostics"]["updates"]["status"] == "unknown"


async def test_runtime_change_invalidates_live_test_evidence(rig):
    client, service, _, _ = rig
    first = await service.run("models")
    (client.settings.state_dir / "validation.json").write_text(json.dumps({
        "fingerprint": first["diagnostics"]["environment_fingerprint"], "validated_at": "test"}))
    assert (await service.run("models"))["diagnostics"]["real_model_test"]["current_environment_validated"]
    async def new_runtime(args, timeout=30):
        return {"stdout": "new-runtime@2.0", "stderr": "", "exit_code": 0}
    client.cli = new_runtime
    assert not (await service.run("models"))["diagnostics"]["real_model_test"]["current_environment_validated"]


async def test_zero_update_ttl_checks_upstream_on_every_call(rig):
    client, service, _, calls = rig
    client.settings.update_ttl = 0
    await service.run("models")
    await service.run("models")
    assert sum(r.url.host == "lmstudio.ai" for r in calls) == 8


async def test_native_load_and_unload_verified(rig):
    _, service, _, _ = rig
    result = await service.run("load", model="test/model", options={"context_length": 2048})
    assert result["ok"] and result["data"]["state"]["verified"]
    assert result["data"]["configuration_verified"]
    result = await service.run("unload", instance_id="instance-1")
    assert result["ok"] and not result["data"]["state"]["loaded"]


async def test_ignored_load_config_is_visible(rig):
    _, service, state, _ = rig
    state["ignored_config"] = True
    result = await service.run("load", model="test/model", options={"context_length": 2048})
    assert not result["ok"]
    assert not result["data"]["configuration_verified"]


async def test_http_success_does_not_prove_unload(rig):
    _, service, state, _ = rig
    state["models"][0]["loaded_instances"] = [{"id": "instance-1"}]
    state["unload_stuck"] = True
    result = await service.run("unload", instance_id="instance-1")
    assert not result["ok"]


@pytest.mark.parametrize("options", [{"context_length": 999999}, {"flash_attention": True}])
async def test_model_load_capabilities(rig, options):
    _, service, _, calls = rig
    result = await service.run("load", model="test/model", options=options)
    assert not result["ok"]
    assert all(r.method == "GET" for r in calls)


async def test_chat_parameters_and_continuation_really_transmitted(rig):
    _, service, _, calls = rig
    options = {"temperature": 0.2, "max_output_tokens": 27, "reasoning": "off",
               "system_prompt": "Be concise", "previous_response_id": "resp_previous", "store": True}
    result = await service.run("chat", model="test/model", input="Hello", options=options)
    assert result["ok"]
    sent = json.loads(next(r.content for r in calls if r.url.path == "/api/v1/chat"))
    assert all(sent[k] == v for k, v in options.items())


async def test_unsupported_reasoning_blocked(rig):
    _, service, _, calls = rig
    result = await service.run("chat", model="test/model", input="Hi", options={"reasoning": "high"})
    assert not result["ok"]
    assert all(r.method == "GET" for r in calls)


async def test_integrations_require_explicit_allowlist(rig):
    _, service, _, _ = rig
    result = await service.run("chat", model="test/model", input="Hi",
                               integrations=[{"id": "mcp/shell", "allowed_tools": ["exec"]}])
    assert not result["ok"]


async def test_chat_model_cannot_be_used_for_embeddings(rig):
    _, service, _, _ = rig
    assert not (await service.run("embeddings", model="test/model", input="Hi"))["ok"]


async def test_download_status_endpoint(rig):
    _, service, _, _ = rig
    assert (await service.run("download", model="catalog/new"))["data"]["status"] == "downloading"
    assert (await service.run("download_status", job_id="job_x"))["data"]["status"] == "completed"


async def test_cli_rejects_unsupported_flags_before_loading(rig, monkeypatch):
    client, _, _, _ = rig
    async def cli(args, timeout=30):
        assert args == ["load", "--help"]
        return {"stdout": "--context-length --identifier --yes"}
    monkeypatch.setattr(client, "cli", cli)
    with pytest.raises(ConnectorError, match="--parallel"):
        await client.advanced_args("test/model", AdvancedLoadOptions(parallel=2))


@pytest.mark.parametrize("arg", ["--help", "-y", "x\ny", "x\x00y"])
def test_cli_arguments_cannot_inject_flags(arg):
    with pytest.raises(ConnectorError):
        Client.argument(arg)
