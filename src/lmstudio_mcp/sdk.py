import asyncio
import inspect
from urllib.parse import urlsplit

import jsonschema
import lmstudio
import msgspec

from .client import ConnectorError
from .errors import error_kinds


def schema():
    data = msgspec.json.schema(lmstudio.LlmLoadModelConfig)
    for definition in data.get("$defs", {}).values():
        if definition.get("type") == "object":
            definition["additionalProperties"] = False
    return data


def configuration_mismatches(requested, actual, prefix=""):
    differences = {}
    for key, value in requested.items():
        name = prefix + key
        found = actual.get(key) if isinstance(actual, dict) else None
        if isinstance(value, dict) and isinstance(found, dict):
            differences.update(configuration_mismatches(value, found, name + "."))
        elif found != value:
            differences[name] = {"requested": value, "actual": found}
    return differences


async def model_config(client, models, action, model=None, instance_id=None, config=None):
    if action == "schema":
        return {"config_schema": schema(), "api_token_supported": "api_token" in inspect.signature(lmstudio.AsyncClient).parameters,
                "source": "Installed official lmstudio Python SDK", "configuration_phase": "load-time"}
    kwargs = {}
    if client.active_token:
        if "api_token" not in inspect.signature(lmstudio.AsyncClient).parameters:
            raise ConnectorError("Installed SDK constructor does not support api_token despite documentation. Use native REST tools with authentication.")
        kwargs["api_token"] = client.active_token
    parsed = urlsplit(client.active_url)
    if parsed.scheme != "http":
        raise ConnectorError("This SDK adapter supports HTTP/WS targets; use REST for HTTPS endpoints")
    if action == "load":
        if not model or not instance_id:
            raise ConnectorError("model and a new instance_id are required")
        meta = client.model(models, model, "llm")
        if any(i.get("id") == instance_id for m in models for i in m["loaded_instances"]):
            raise ConnectorError("Instance ID already exists; inspect it or choose a new ID")
        config = config or {}
        try:
            jsonschema.validate(config, schema())
        except jsonschema.ValidationError as e:
            raise ConnectorError(f"Unsupported SDK configuration at {list(e.absolute_path)}") from None
        if config.get("contextLength", 0) > meta.get("max_context_length", float("inf")):
            raise ConnectorError("Context exceeds model maximum")
        if meta.get("format") == "mlx" and any(k != "contextLength" for k in config):
            raise ConnectorError("The extra SDK load options target GGUF; use CLI options for supported MLX features")
    elif not instance_id or not any(i.get("id") == instance_id for m in models for i in m["loaded_instances"]):
        raise ConnectorError("inspect requires an existing loaded instance_id; no implicit loading")
    try:
        async with asyncio.timeout(client.settings.timeout):
            async with lmstudio.AsyncClient(parsed.netloc, **kwargs) as sdk:
                if action == "load":
                    handle = await sdk.llm.load_new_instance(model, instance_identifier=instance_id, config=config)
                else:
                    meta = client.model(models, instance_id)
                    namespace = sdk.llm if meta["type"] == "llm" else sdk.embedding
                    handles = await namespace.list_loaded()
                    handle = next((h for h in handles if h.identifier == instance_id), None)
                    if handle is None:
                        raise ConnectorError("Instance disappeared before SDK inspection")
                actual = msgspec.to_builtins(await handle.get_load_config())
                context = await handle.get_context_length()
        state = await client.verify_instance(instance_id, True)
    except ConnectorError:
        raise
    except Exception as exc:  # noqa: BLE001 - sanitize provider exception groups that can contain prompts or credentials
        raise ConnectorError(f"LM Studio SDK {error_kinds(exc)}; inspect model state and lm_diagnose before retrying") from None
    mismatches = configuration_mismatches(config or {}, actual)
    rest_context = state["instance"].get("config", {}).get("context_length")
    return {"instance_id": instance_id, "sdk_context_length": context, "sdk_config": actual,
            "rest_context_length": rest_context, "interfaces_agree": rest_context == context,
            "configuration_mismatches": mismatches, "state": state}
