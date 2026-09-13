import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote, urlsplit

from .client import Client, ConnectorError
from .config import AdvancedLoadOptions, ChatOptions, Integration, LoadOptions
from .connections import Connections
from .diagnostics import diagnose, journal
from .integrations import Integrations
from .knowledge import Knowledge
from .rag import Rag
from .sdk import model_config
from .storage import atomic_json, file_lock, read_json


class Service:
    def __init__(self, client: Client):
        self.client = client

    async def run(self, operation, **args):
        c = self.client
        # Serialize local mutations and inference so load/unload cannot race within this server.
        async with c.lock:
            try:
                try:
                    models = await c.preflight(
                        require_api=operation not in {"status", "server", "runtime", "integrations", "diagnose",
                            "connections", "docs", "mcp_config", "mcp_probe", "mcp_call", "profiles", "rag_manage", "link", "import", "model_config"},
                        force_updates=args.get("refresh_updates", False),
                    )
                except ConnectorError as exc:
                    if operation not in {"connections", "diagnose", "docs", "mcp_config"}:
                        raise
                    models = []
                    c.current_health = {"api_error": str(exc), "api_v1": "unavailable"}
                data = await self.dispatch(operation, models, **args)
                if isinstance(data, dict) and data.get("configuration_mismatches"):
                    journal(c, operation, "Requested configuration does not match effective model configuration")
                    return {"ok": False, "error": "Model loaded, but LM Studio did not apply the requested configuration",
                            "data": data, "diagnostics": c.current_health}
                if isinstance(data, dict) and data.get("status") == "failed":
                    return {"ok": False, "error": "LM Studio reported operation failure",
                            "data": data, "diagnostics": c.current_health}
                return {"ok": True, "data": data, "diagnostics": c.current_health}
            except (ConnectorError, ValueError, TimeoutError, OSError, sqlite3.Error) as exc:
                message = str(exc) if str(exc) else "Operation timed out; check resulting state before retrying"
                journal(c, operation, message)
                return {"ok": False, "error": c.redact(message), "diagnostics": c.current_health}

    async def dispatch(self, operation, models, **args):
        c = self.client
        if operation == "docs":
            return await Knowledge(c.settings).run(**args)
        if operation == "diagnose":
            return await diagnose(c, models, **args)
        if operation == "connections":
            return await Connections(c.settings).run(**args)
        if operation == "model_config":
            return await model_config(c, models, **args)
        if operation == "mcp_config":
            return Integrations(c.settings).configure(**args)
        if operation == "mcp_probe":
            return await Integrations(c.settings).inspect_or_call(args["name"])
        if operation == "mcp_call":
            return await Integrations(c.settings).inspect_or_call(**args)
        if operation == "rag_index":
            return await Rag(c).index(models, **args)
        if operation == "rag_search":
            return await Rag(c).search(models, **args)
        if operation == "rag_ask":
            return await Rag(c).ask(models, **args)
        if operation == "rag_manage":
            return Rag(c).manage(**args)
        if operation == "profiles":
            action, name = args["action"], args.get("name")
            path = c.settings.state_dir / "model-profiles.json"
            if action == "load":
                profile = read_json(path).get(name)
                if not profile:
                    raise ConnectorError("Unknown model profile")
                return await model_config(c, models, "load", profile["model"], args.get("instance_id") or "profile-" + name, profile["config"])
            with file_lock(c.settings.state_dir):
                profiles = read_json(path)
                if action == "save":
                    import jsonschema

                    from .sdk import schema
                    if not name or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) or not args.get("model"):
                        raise ConnectorError("Profile name and model required")
                    try:
                        jsonschema.validate(args.get("config") or {}, schema())
                    except jsonschema.ValidationError:
                        raise ConnectorError("Unsupported model profile configuration; inspect lm_model_config schema") from None
                    profiles[name] = {"model": args["model"], "config": args.get("config") or {}}
                elif action == "delete":
                    profiles.pop(name, None)
                if action != "list":
                    atomic_json(path, profiles)
            return {"profiles": profiles}
        if operation == "link":
            action = args["action"]
            command = ["link", action]
            if action in {"set-device-name", "set-preferred-device"}:
                command.append(c.argument(args.get("value") or ""))
            help_result = await c.cli(["link", "--help"])
            if action not in help_result["stdout"]:
                raise ConnectorError("Installed lms does not support this LM Link operation")
            result = await c.cli(command)
            return {"result": result, "status": await c.cli(["link", "status"]),
                    "note": "LM Link may require account sign-in in the app; no credentials are handled by this tool."}
        if operation == "import":
            path = Path(args["path"]).expanduser().resolve()
            if path.suffix.lower() != ".gguf" or not path.is_file():
                raise ConnectorError("Import requires a local GGUF file")
            with path.open("rb") as f:
                if f.read(4) != b"GGUF":
                    raise ConnectorError("File has no GGUF signature")
            command = ["import", str(path), "--copy", "--yes"]
            if args.get("user_repo"):
                command += ["--user-repo", c.argument(args["user_repo"])]
            if args.get("dry_run", True):
                command.append("--dry-run")
            result = await c.cli(command, timeout=c.settings.timeout)
            return {"result": result, "dry_run": args.get("dry_run", True),
                    "inventory": await c.cli(["ls", "--json"]), "note": "Source file is copied, never moved."}
        if operation == "status":
            return {"models": models, "lms_available": c.settings.lms_path.is_file(),
                    "server_url": c.active_url, "updates": await c.check_updates(),
                    "runtime": c.runtime_status,
                    "server": await c.cli(["server", "status", "--json"]) if c.active_local else {"remote_api_only": True}}
        if operation == "models":
            return models if not args.get("model") else c.model(models, args["model"])
        if operation == "load":
            model = c.model(models, args["model"])
            options = LoadOptions.model_validate(args.get("options") or {}).model_dump(exclude_none=True)
            self.check_context(model, options)
            if model.get("format") == "mlx" and any(k != "context_length" for k in options):
                raise ConnectorError("These load settings apply to llama.cpp/GGUF; this model uses MLX")
            result = await c.request("POST", "/api/v1/models/load",
                                     {"model": args["model"], **options, "echo_load_config": True})
            state = await c.verify_instance(result.get("instance_id"), True)
            actual = result.get("load_config", {})
            mismatches = {k: {"requested": v, "actual": actual.get(k)} for k, v in options.items()
                          if actual.get(k) != v}
            return {"result": result, "state": state, "configuration_verified": not mismatches,
                    "configuration_mismatches": mismatches}
        if operation in {"advanced_load", "estimate"}:
            model = c.model(models, args["model"])
            options = AdvancedLoadOptions.model_validate(args.get("options") or {})
            self.check_context(model, options.model_dump(exclude_none=True))
            if operation == "advanced_load" and not options.identifier:
                raise ConnectorError("Set options.identifier for advanced loading so the resulting instance can be verified")
            command = await c.advanced_args(args["model"], options, estimate=operation == "estimate")
            result = await c.cli(command, timeout=c.settings.timeout)
            if operation == "estimate":
                return result
            state = await c.verify_instance(options.identifier, True)
            actual = state["instance"].get("config", {})
            requested = options.model_dump(exclude_none=True)
            mismatches = {k: {"requested": v, "actual": actual.get(k)} for k, v in requested.items()
                          if k in {"context_length", "parallel"} and actual.get(k) != v}
            return {"cli": result, "state": state, "configuration_mismatches": mismatches,
                    "unverified_options": [k for k in requested if k not in {"context_length", "parallel", "identifier"}],
                    "note": "Instance and exposed context/parallel values verified. Other flags need engine-specific validation."}
        if operation == "unload":
            instance_id = args["instance_id"]
            if not any(i.get("id") == instance_id for m in models for i in m["loaded_instances"]):
                return {"instance_id": instance_id, "loaded": False, "verified": True, "already_unloaded": True}
            result = await c.request("POST", "/api/v1/models/unload", {"instance_id": instance_id})
            return {"result": result, "state": await c.verify_instance(instance_id, False)}
        if operation == "download":
            payload = {"model": args["model"]}
            if args.get("quantization"):
                payload["quantization"] = args["quantization"]
            result = await c.request("POST", "/api/v1/models/download", payload)
            if result.get("status") not in {"downloading", "paused", "completed", "failed", "already_downloaded"}:
                raise ConnectorError("Unrecognized download response; check job status before retrying")
            return result
        if operation == "download_status":
            return await c.request("GET", "/api/v1/models/download/status/" + quote(args["job_id"], safe=""))
        if operation == "chat":
            model = c.model(models, args["model"], "llm")
            options = ChatOptions.model_validate(args.get("options") or {}).model_dump(exclude_none=True)
            self.check_context(model, options)
            if options.get("reasoning"):
                allowed = model.get("capabilities", {}).get("reasoning", {}).get("allowed_options", [])
                if options["reasoning"] not in allowed:
                    raise ConnectorError(f"Reasoning option unsupported by this model. Allowed: {allowed}")
            input_value = args["input"]
            if isinstance(input_value, list):
                for item in input_value:
                    if item.get("type") == "image":
                        if not model.get("capabilities", {}).get("vision"):
                            raise ConnectorError("Selected model has no vision capability")
                        if not isinstance(item.get("data_url"), str) or not item["data_url"].startswith("data:image/"):
                            raise ConnectorError("Images must be data:image/... URLs")
                    elif item.get("type") != "text" or not isinstance(item.get("content"), str):
                        raise ConnectorError("Native input items must be text/content or image/data_url")
            payload = {"model": args["model"], "input": input_value, "stream": False, **options}
            if args.get("integrations"):
                plugins = [Integration.model_validate(i) for i in args["integrations"]]
                for plugin in plugins:
                    granted = Integrations(c.settings).authorized_tools(plugin.id.removeprefix("mcp/"))
                    if not set(plugin.allowed_tools).issubset(granted) and plugin.id not in c.settings.allowed_integrations:
                        raise ConnectorError(f"Integration {plugin.id} not enabled in LM_MCP_ALLOWED_INTEGRATIONS")
                payload["integrations"] = [{"type": "plugin", **p.model_dump()} for p in plugins]
            result = await c.request("POST", "/api/v1/chat", payload)
            if not isinstance(result.get("output"), list):
                raise ConnectorError("Native chat response schema changed: missing output array")
            return result
        if operation == "embeddings":
            c.model(models, args["model"], "embedding")
            result = await c.request("POST", "/v1/embeddings", {"model": args["model"], "input": args["input"]})
            if not isinstance(result.get("data"), list) or not result["data"]:
                raise ConnectorError("Embedding response missing vectors")
            return result
        if operation == "openai_chat":
            c.model(models, args["model"], "llm")
            payload = {"model": args["model"], "messages": args["messages"], "stream": False,
                       "max_tokens": args["max_tokens"]}
            for key in ("temperature", "tools", "tool_choice", "response_format"):
                if args.get(key) is not None:
                    payload[key] = args[key]
            result = await c.request("POST", "/v1/chat/completions", payload)
            if not isinstance(result.get("choices"), list) or not result["choices"]:
                raise ConnectorError("Chat completion response missing choices")
            return result
        if operation == "server":
            action = args["action"]
            if action == "status":
                return await c.cli(["server", "status", "--json"])
            port = args.get("port") or urlsplit(c.settings.base_url).port or 80
            if action == "restart":
                await c.cli(["server", "stop"])
                action = "start"
            command = ["server", "start", "--port", str(port), "--bind", "127.0.0.1"] if action == "start" else ["server", "stop"]
            result = await c.cli(command)
            await c.preflight(require_api=action == "start")
            status = await c.cli(["server", "status", "--json"])
            parsed = json.loads(status["stdout"])
            if parsed.get("running") is not (action == "start"):
                raise ConnectorError("Server command completed but resulting server state is incorrect")
            return {"command": result, "status": parsed, "verified": True}
        if operation == "runtime":
            action = args["action"]
            if action == "list":
                return await c.cli(["runtime", "ls"])
            if action == "hardware":
                return await c.cli(["runtime", "survey", "--json"])
            if action == "available":
                return await c.cli(["runtime", "get", "--list", "--channel", "stable"])
            help_text = (await c.cli(["runtime", "update", "--help"]))["stdout"]
            if "--dry-run" not in help_text or "--channel" not in help_text:
                raise ConnectorError("Installed runtime update CLI not compatible")
            command = ["runtime", "update", "--all", "--channel", "stable"]
            command += ["--yes"] if action == "update" else ["--dry-run"]
            result = await c.cli(command, timeout=900 if action == "update" else 60)
            return {"result": result, "installed": await c.cli(["runtime", "ls"]),
                    "inference_retest_required": action == "update"}
        if operation == "integrations":
            return Integrations(c.settings).configure("list")
        raise ConnectorError("Unknown operation")

    @staticmethod
    def check_context(model, options):
        length = options.get("context_length")
        if length and model.get("max_context_length") and length > model["max_context_length"]:
            raise ConnectorError(f"Requested context exceeds model maximum {model['max_context_length']}")
