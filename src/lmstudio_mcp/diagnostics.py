import json
import platform
import re
import shutil
from pathlib import Path

from .client import ConnectorError, now
from .integrations import Integrations
from .knowledge import Knowledge
from .storage import atomic_json, file_lock, read_json

ISSUES = {
    "authentication": {"patterns": ["authentication", "permissions", "401", "403"],
                       "next_steps": ["Check token environment variable and LM Studio token permissions", "lm_connections(action='test')"]},
    "network": {"patterns": ["unreachable", "connect", "refused", "network"],
                "next_steps": ["Compare configured origin and actual server port", "lm_server_control(action='status')", "lm_connections(action='test')"]},
    "timeout": {"patterns": ["timed out", "timeout"],
                "next_steps": ["Check actual loaded instances and resources before retrying", "lm_diagnose(include_hardware=true)"]},
    "memory": {"patterns": ["out of memory", "insufficient memory", "allocation failed", "oom"],
               "next_steps": ["Unload an unused instance", "lm_estimate", "Reduce context/parallelism or use a smaller model"]},
    "context": {"patterns": ["context", "configuration", "setting"],
                "next_steps": ["lm_model_config(action='inspect')", "Compare SDK and REST reported values", "Do not treat HTTP success as applied settings"]},
    "mcp": {"patterns": ["mcp", "tool"],
            "next_steps": ["lm_mcp_config(action='list')", "lm_mcp_probe", "Check plugin permissions and exact authorized tool names"]},
    "schema": {"patterns": ["schema", "invalid json", "unsupported"],
               "next_steps": ["lm_docs(action='sync')", "Compare installed SDK/CLI schemas to official docs", "Do not silently drop unsupported options"]},
    "rag": {"patterns": ["embedding", "index", "document", "citation"],
            "next_steps": ["lm_rag_manage(action='list')", "Check document text extraction and hashes", "Reindex after embedding model/runtime changes"]},
}


def journal(client, operation, message):
    # Store neither arguments nor model outputs. Only bounded connector-generated errors.
    try:
        with file_lock(client.settings.state_dir):
            path = client.settings.state_dir / "errors.json"
            rows = read_json(path, [])
            rows.append({"at": now(), "operation": operation, "error": client.redact(message)[:1000]})
            atomic_json(path, rows[-50:])
    except (OSError, ValueError):
        pass  # Diagnostics must not hide the original operation failure.


async def diagnose(client, models, include_hardware=False):
    async def attempt(args):
        try:
            return await client.cli(args)
        except (ConnectorError, TimeoutError, OSError) as e:
            return {"error": str(e)}
    status = await attempt(["server", "status", "--json"])
    try:
        server = json.loads(status["stdout"]) if "stdout" in status else status
    except ValueError:
        server = status
    recent = read_json(client.settings.state_dir / "errors.json", [])[-10:]
    errors = " ".join(r["error"] for r in recent) + " " + (client.current_health.get("api_error") or "")
    findings = [{"category": name, "basis": "connector error history", "status": "hypothesis_to_check",
                 "next_steps": definition["next_steps"]} for name, definition in ISSUES.items()
                if any(p in errors.lower() for p in definition["patterns"])]
    log_signals = []
    if client.active_local:
        root = Path.home() / ".lmstudio/server-logs"
        paths = sorted(root.glob("*/*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
        for path in paths:
            try:
                with path.open("rb") as f:
                    f.seek(max(0, path.stat().st_size - 128 * 1024))
                    lines = f.read().decode(errors="replace").splitlines()
                # Only count severity-tagged entries. No prompt, output or log line is returned.
                error_lines = [line.lower() for line in lines if re.search(r"\[(?:ERROR|WARN|WARNING)\]", line)]
                counts = {name: sum(any(p in line for p in item["patterns"]) for line in error_lines)
                          for name, item in ISSUES.items()}
                log_signals.append({"file": str(path), "severity_entries": len(error_lines),
                                    "category_counts": {k: v for k, v in counts.items() if v}})
            except OSError:
                pass
    disk = shutil.disk_usage(client.settings.state_dir.parent)
    result = {"checked_at": now(), "connection": client.active_profile, "server": server,
              "local_host": {"system": platform.system(), "machine": platform.machine(), "disk_free_bytes": disk.free},
              "loaded_instances": [{"model": m["key"], **i} for m in models for i in m["loaded_instances"]],
              "recent_connector_errors": recent, "log_signals": log_signals, "findings": findings,
              "documentation": await Knowledge(client.settings).metadata(),
              "note": "Historical errors and log matches are clues, not proof of a current fault. No raw prompts/log lines returned."}
    if include_hardware:
        result["hardware"] = await attempt(["runtime", "survey", "--json"])
    try:
        result["mcp_configuration"] = Integrations(client.settings).configure("list")
    except (ConnectorError, ValueError, OSError):
        result["mcp_configuration"] = {"error": "MCP configuration cannot be read; inspect lm_mcp_config"}
    return result
