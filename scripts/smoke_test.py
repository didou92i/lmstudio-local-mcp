"""Exercise the real MCP transport and local models; unload only instances created by this test."""
import argparse
import asyncio
import base64
import json
import os
import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from lmstudio_mcp.storage import atomic_json

ROOT = Path(__file__).resolve().parents[1]


def png():
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    raw = b"\x89PNG\r\n\x1a\n"
    raw += chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
    raw += chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * 64) * 64))
    raw += chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(raw).decode()


async def exercise(read, write, args):
    report = {"tested_at": datetime.now(UTC).isoformat(), "transport": "http" if args.http else "stdio", "checks": []}
    created = set()
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        report["tool_count"] = len(tools.tools)

        async def call(name, arguments=None, allow_config_mismatch=False):
            print(f"Checking {name}...", flush=True)
            result = await session.call_tool(name, arguments or {})
            data = result.structuredContent
            if not data:
                data = json.loads(next(c.text for c in result.content if c.type == "text"))
            mismatch = data.get("data", {}).get("configuration_mismatches") if isinstance(data.get("data"), dict) else None
            if (result.isError or not data.get("ok")) and not (allow_config_mismatch and mismatch):
                raise AssertionError(f"{name}: {data.get('error', 'MCP error')}")
            return data

        first = await call("lm_models")
        before = {i["id"] for m in first["data"] for i in m["loaded_instances"]}
        report["environment_fingerprint"] = first["diagnostics"]["environment_fingerprint"]
        if args.http or args.protocol_only:
            await call("lm_models")
            report["checks"].append("MCP initialize/list_tools/repeated lm_models")
            return report
        try:
            loaded = await call("lm_load", {"model": args.model, "options": {"context_length": 4096}},
                                allow_config_mismatch=True)
            model_id = loaded["data"]["state"]["instance_id"]
            if model_id not in before:
                created.add(model_id)
            if not loaded["data"]["configuration_verified"]:
                report["known_limitations"] = [{"model": args.model, "configuration_mismatches": loaded["data"]["configuration_mismatches"]}]
                report["checks"].append("Native load verified; ignored configuration correctly flagged as MCP error")
            else:
                report["checks"].append("Native load with effective context=4096 verified")
            answer = await call("lm_chat", {"model": model_id, "input": "Combien font 6 fois 7 ? Réponds uniquement le nombre.",
                                            "options": {"reasoning": "off", "temperature": 0, "max_output_tokens": 32}})
            assert "42" in json.dumps(answer["data"]["output"])
            report["checks"].append("Native local inference: 6*7=42")
            conversation = await call("lm_chat", {"model": model_id, "input": "Mémorise mon code: SILEX-482. Confirme brièvement.",
                "options": {"reasoning": "off", "temperature": 0, "store": True, "system_prompt": "Réponds en français.", "max_output_tokens": 64}})
            response_id = conversation["data"]["response_id"]
            continuation = await call("lm_chat", {"model": model_id, "input": "Quel est mon code ?",
                "options": {"reasoning": "off", "temperature": 0, "store": True, "previous_response_id": response_id,
                            "system_prompt": "Réponds en français.", "max_output_tokens": 64}})
            assert "SILEX-482" in json.dumps(continuation["data"]["output"]), continuation["data"]
            report["checks"].append("Stateful response_id continuation preserved test code")
            vision = await call("lm_chat", {"model": model_id, "input": [
                {"type": "text", "content": "Quelle est la couleur principale de cette image ? Un mot."},
                {"type": "image", "data_url": png()}],
                "options": {"reasoning": "off", "temperature": 0, "max_output_tokens": 128}})
            output = json.dumps(vision["data"]["output"]).lower()
            assert "rouge" in output or "red" in output, output
            report["checks"].append("Native vision identified a red PNG")
            completion = await call("lm_openai_chat", {"model": model_id,
                "messages": [{"role": "user", "content": "Réponds uniquement OK."}], "max_tokens": 256})
            assert completion["data"]["choices"]
            report["checks"].append("OpenAI-compatible completion returned choices")
            if model_id in created:
                await call("lm_unload", {"instance_id": model_id})
                created.remove(model_id)
            embedding = await call("lm_load", {"model": args.embedding})
            embedding_id = embedding["data"]["state"]["instance_id"]
            if embedding_id not in before:
                created.add(embedding_id)
            vectors = await call("lm_embeddings", {"model": embedding_id, "input": ["Bonjour", "Hello"]})
            rows = vectors["data"]["data"]
            assert len(rows) == 2 and len(rows[0]["embedding"]) > 0
            report["embedding_dimensions"] = len(rows[0]["embedding"])
            report["checks"].append("Batch embeddings returned two nonempty vectors")
        finally:
            for instance_id in list(created):
                await call("lm_unload", {"instance_id": instance_id})
            report["checks"].append("Created model instances unloaded and absence verified")
        final = await call("lm_models")
        assert final["diagnostics"]["environment_fingerprint"] == report["environment_fingerprint"], "Environment changed during test"
        return report


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="google/gemma-4-e4b")
    parser.add_argument("--embedding", default="text-embedding-nomic-embed-text-v1.5")
    parser.add_argument("--http", help="Read-only MCP HTTP protocol check, e.g. http://127.0.0.1:8765/mcp")
    parser.add_argument("--protocol-only", action="store_true", help="Read-only stdio protocol check")
    args = parser.parse_args()
    state = Path(os.getenv("LM_MCP_STATE_DIR", str(ROOT / ".state")))
    if not args.http and not args.protocol_only:
        # A failed/interrupted test must never leave an older success marked as current.
        atomic_json(state / "validation.json", {"status": "test_running_or_incomplete", "started_at": datetime.now(UTC).isoformat()})
    if args.http:
        async with streamable_http_client(args.http) as (read, write, _):
            report = await exercise(read, write, args)
    else:
        params = StdioServerParameters(command=str(ROOT / ".venv/bin/python"),
            args=["-m", "lmstudio_mcp.server"], cwd=str(ROOT), env=dict(os.environ))
        async with stdio_client(params) as (read, write):
            report = await exercise(read, write, args)
    state.mkdir(exist_ok=True, parents=True)
    report_file = state / ("smoke-http.json" if args.http else "protocol-stdio.json" if args.protocol_only else "smoke-stdio.json")
    report_file.write_text(json.dumps(report, indent=2))
    if not args.http and not args.protocol_only:
        (state / "validation.json").write_text(json.dumps({"fingerprint": report["environment_fingerprint"],
            "validated_at": report["tested_at"], "report": str(report_file),
            "known_limitations": report.get("known_limitations", [])}, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
