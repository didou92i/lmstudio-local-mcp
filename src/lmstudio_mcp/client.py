import asyncio
import hashlib
import json
import os
import plistlib
import re
import signal
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from importlib.metadata import version as package_version
from pathlib import Path

import httpx

from .config import AdvancedLoadOptions, Settings

TESTED_VERSION = "0.4.24"
SOURCES = {
    "changelog": "https://lmstudio.ai/changelog/lmstudio",
    "api": "https://lmstudio.ai/docs/developer/rest",
    "load": "https://lmstudio.ai/docs/developer/rest/load",
    "chat": "https://lmstudio.ai/docs/developer/rest/chat",
}


def now():
    return datetime.now(UTC).isoformat()


class ConnectorError(Exception):
    pass


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def visible_text(html):
    parser = VisibleText()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def latest_version(html):
    versions = re.findall(r"LM Studio\s+(\d+\.\d+\.\d+)", visible_text(html))
    if not versions:
        raise ConnectorError("Official changelog format unrecognized; latest version unknown")
    return max(versions, key=lambda x: tuple(map(int, x.split("."))))


class Client:
    def __init__(self, settings: Settings, transport=None, remote_transport=None):
        self.settings = settings
        self.active_url = settings.base_url
        self.active_token = settings.api_token
        self.active_local = True
        self.active_profile = "local"
        headers = {"Authorization": f"Bearer {settings.api_token}"} if settings.api_token else {}
        self.http = httpx.AsyncClient(
            base_url=settings.base_url, headers=headers, timeout=settings.timeout,
            trust_env=False, transport=transport,
        )
        # Separate client: never send the local API token to update/documentation sites.
        self.remote = httpx.AsyncClient(
            timeout=8, follow_redirects=True, trust_env=False, transport=remote_transport,
        )
        self.lock = asyncio.Lock()
        self.updates_lock = asyncio.Lock()
        self.updates = {}
        self.last_probe = None
        self.last_app_version = None
        self.current_health = {}
        self.runtime_status = {}
        source_hash = hashlib.sha256()
        source_root = Path(__file__).parent
        for path in sorted([*source_root.glob("*.py"), *(source_root / "data").glob("*.json")]):
            source_hash.update(path.name.encode() + path.read_bytes())
        self.connector_fingerprint = source_hash.hexdigest()
        self.dependency_versions = {name: package_version(name) for name in ("lmstudio", "mcp", "httpx", "pypdf", "python-docx")}
        try:
            self.updates = json.loads((settings.state_dir / "updates.json").read_text())
        except (OSError, ValueError):
            pass

    async def close(self):
        await self.http.aclose()
        await self.remote.aclose()

    def redact(self, text):
        if self.settings.api_token:
            text = text.replace(self.settings.api_token, "[REDACTED]")
        if self.active_token:
            text = text.replace(self.active_token, "[REDACTED]")
        return text

    async def request(self, method, endpoint, payload=None, timeout=None):
        try:
            kwargs = {"json": payload} if payload is not None else {}
            if timeout is not None:
                kwargs["timeout"] = timeout
            response = await self.http.request(method, endpoint, **kwargs)
            if response.status_code in {401, 403}:
                raise ConnectorError("LM Studio authentication/permissions required. Set LM_STUDIO_API_TOKEN.")
            if response.is_error:
                # Do not echo server bodies: they may contain prompts or credentials.
                raise ConnectorError(f"LM Studio HTTP {response.status_code} for {endpoint}; operation not verified")
            result = response.json()
            if not isinstance(result, dict):
                raise ConnectorError(f"Unexpected response schema from {endpoint}")
            if result.get("error"):
                raise ConnectorError(f"LM Studio returned an error for {endpoint}; operation not verified")
            return result
        except httpx.TimeoutException:
            raise ConnectorError("LM Studio request timed out. Outcome unknown; check status before retrying a mutation.") from None
        except httpx.RequestError:
            raise ConnectorError("LM Studio API unreachable. Check lm_server_control(action='status'/'start').") from None
        except ValueError:
            raise ConnectorError(f"Invalid JSON response from {endpoint}") from None

    def app_version(self):
        try:
            with self.settings.app_plist.open("rb") as f:
                return plistlib.load(f).get("CFBundleShortVersionString", "unknown")
        except (OSError, ValueError):
            return "unknown"

    async def check_updates(self, force=False):
        async with self.updates_lock:
            age = time.time() - self.updates.get("checked_epoch", 0)
            if not force and self.updates.get("status") == "checked" and age < self.settings.update_ttl:
                return {**self.updates, "cached": True}
            # Retry failures at most once per minute; report them as unknown, never up-to-date.
            if (not force and self.updates.get("status") != "checked" and self.last_probe is not None
                    and time.monotonic() - self.last_probe < 60):
                return {**self.updates, "cached": True}
            self.last_probe = time.monotonic()
            try:
                responses = await asyncio.gather(*(self.remote.get(url) for url in SOURCES.values()))
                for response in responses:
                    response.raise_for_status()
                latest = latest_version(responses[0].text)
                hashes = {key: hashlib.sha256(visible_text(response.text).encode()).hexdigest()
                          for key, response in zip(SOURCES, responses)}
                previous = self.updates.get("document_hashes", {})
                changed = sorted(set(self.updates.get("documents_changed_since_previous_check", [])) |
                                 {k for k in hashes if k in previous and hashes[k] != previous[k]})
                self.updates = {
                    "status": "checked", "latest_lmstudio": latest, "checked_at": now(),
                    "checked_epoch": time.time(), "document_hashes": hashes,
                    "documents_changed_since_previous_check": changed,
                    "sources": SOURCES, "cached": False,
                    "note": "Release/document checks do not prove every new feature works. No automatic installation.",
                }
                self.settings.state_dir.mkdir(parents=True, exist_ok=True)
                target = self.settings.state_dir / "updates.json"
                temp = target.with_suffix(f".{os.getpid()}.tmp")
                temp.write_text(json.dumps(self.updates, indent=2))
                temp.replace(target)
            except (httpx.HTTPError, ConnectorError, OSError):
                self.updates = {
                    **self.updates, "status": "unknown", "attempted_at": now(), "cached": False,
                    "warning": "Cannot verify current official releases/docs. Last known data may be stale.",
                }
            return self.updates

    async def preflight(self, require_api=True, force_updates=False):
        from .connections import Connections
        profile = Connections(self.settings).get()
        self.active_url, self.active_token = profile["url"], profile["token"]
        self.active_local, self.active_profile = profile["local"], profile["name"]
        self.http.base_url = self.active_url
        self.http.headers.pop("Authorization", None)
        if self.active_token:
            self.http.headers["Authorization"] = "Bearer " + self.active_token
        version = self.app_version() if self.active_local else "remote-unknown"
        changed = self.last_app_version is not None and version != self.last_app_version
        self.last_app_version = version
        updates_task = asyncio.create_task(self.check_updates(force_updates or changed))
        models = []
        error = None
        try:
            data = await self.request("GET", "/api/v1/models", timeout=5)
            if not isinstance(data.get("models"), list):
                raise ConnectorError("Native API v1 schema incompatible: models array missing")
            models = data["models"]
            if any(not isinstance(m, dict) or not isinstance(m.get("key"), str)
                   or not isinstance(m.get("loaded_instances"), list) for m in models):
                raise ConnectorError("Native API v1 model schema incompatible")
        except ConnectorError as exc:
            error = str(exc)
        updates = await updates_task
        try:
            self.runtime_status = await self.cli(["runtime", "ls"])
            runtime_fingerprint = hashlib.sha256(self.runtime_status["stdout"].encode()).hexdigest()
        except (ConnectorError, TimeoutError, OSError):
            self.runtime_status = {"status": "unknown"}
            runtime_fingerprint = "unknown"
        fingerprint = hashlib.sha256(
            f"{version}:{runtime_fingerprint}:{self.active_url}:{self.connector_fingerprint}:{self.dependency_versions}".encode()
        ).hexdigest()
        try:
            evidence = json.loads((self.settings.state_dir / "validation.json").read_text())
        except (OSError, ValueError):
            evidence = {}
        validated = evidence.get("fingerprint") == fingerprint and runtime_fingerprint != "unknown"
        warnings = []
        if version.split("+")[0] != TESTED_VERSION:
            warnings.append(f"Installed app version {version}; last integration-tested version {TESTED_VERSION}.")
        if updates.get("status") != "checked":
            warnings.append("Latest upstream version not currently verified.")
        elif version != "unknown" and updates["latest_lmstudio"] != version.split("+")[0]:
            warnings.append(f"Official latest {updates['latest_lmstudio']}; installed {version}.")
        if updates.get("documents_changed_since_previous_check"):
            warnings.append("Official documentation changed; review connector coverage before using new features.")
        if not validated:
            warnings.append("This app/runtime combination needs the real-model smoke test (scripts/smoke_test.py).")
        elif evidence.get("known_limitations"):
            warnings.append("The real-model test found configuration limitations; see real_model_test.known_limitations.")
        self.current_health = {
            "checked_at": now(), "installed_version": version, "integration_tested_version": TESTED_VERSION,
            "connection_profile": self.active_profile, "api_origin": self.active_url,
            "api_v1": "unreachable_or_incompatible" if error else "available",
            "api_error": error, "warnings": warnings,
            "runtime_fingerprint": runtime_fingerprint, "environment_fingerprint": fingerprint,
            "connector_fingerprint": self.connector_fingerprint, "dependencies": self.dependency_versions,
            "real_model_test": {"current_environment_validated": validated,
                                "last_validated_at": evidence.get("validated_at"),
                                "report": evidence.get("report") if validated else None,
                                "known_limitations": evidence.get("known_limitations", []) if validated else []},
            "updates": {k: v for k, v in updates.items() if k not in {"document_hashes", "sources"}},
            "verification_scope": "Live model-list schema per call; model operation postconditions where applicable."
        }
        if require_api and error:
            raise ConnectorError(error)
        return models

    @staticmethod
    def model(models, key, expected_type=None):
        for model in models:
            ids = [model["key"], *model.get("variants", []),
                   *(i.get("id") for i in model.get("loaded_instances", []))]
            if key in ids:
                if expected_type and model.get("type") != expected_type:
                    raise ConnectorError(f"Model must be of type {expected_type}")
                return model
        raise ConnectorError(f"Unknown model or instance: {key}. Use lm_models first.")

    async def verify_instance(self, instance_id, present):
        if not isinstance(instance_id, str) or not instance_id:
            raise ConnectorError("Operation returned no instance ID; resulting state not verified")
        for attempt in range(5):
            data = await self.request("GET", "/api/v1/models", timeout=5)
            if not isinstance(data.get("models"), list):
                raise ConnectorError("Cannot verify resulting model state: missing models array")
            instances = [i for m in data["models"] for i in m.get("loaded_instances", [])]
            found = next((i for i in instances if i.get("id") == instance_id), None)
            if bool(found) == present:
                return {"instance_id": instance_id, "verified": True, "loaded": present,
                        "instance": found, "checked_at": now()}
            if attempt < 4:
                await asyncio.sleep(0.3)
        raise ConnectorError("API request succeeded but resulting load/unload state could not be verified")

    @staticmethod
    def argument(value):
        value = str(value)
        if not value or value.startswith("-") or "\x00" in value or "\n" in value:
            raise ConnectorError("Invalid CLI argument")
        return value

    async def cli(self, args, timeout=30):
        if not self.active_local:
            raise ConnectorError("Local CLI administration disabled for a remote connection profile")
        path = self.settings.lms_path
        if not path.is_file():
            raise ConnectorError("lms CLI missing. Configure LMS_PATH.")
        try:
            process = await asyncio.create_subprocess_exec(
                str(path), *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL, start_new_session=True,
            )
        except OSError:
            raise ConnectorError("Cannot start lms CLI") from None
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except (TimeoutError, asyncio.CancelledError):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            raise
        output = self.redact(stdout.decode(errors="replace"))
        errors = self.redact(stderr.decode(errors="replace"))
        if process.returncode:
            raise ConnectorError(f"lms exited with {process.returncode}: {errors[-1500:] or output[-1500:]}")
        return {"stdout": output[-20000:], "stderr": errors[-3000:], "exit_code": process.returncode}

    async def advanced_args(self, model, options: AdvancedLoadOptions, estimate=False):
        help_text = (await self.cli(["load", "--help"]))["stdout"]
        args = ["load", self.argument(model)]
        for name, value in options.model_dump(exclude_none=True).items():
            flag = "--" + name.replace("_", "-")
            if isinstance(value, bool):
                if not value:
                    flag = "--no-" + name.replace("_", "-")
                if flag not in help_text:
                    raise ConnectorError(f"Installed lms does not support {flag}; no command executed")
                args.append(flag)
            else:
                if flag not in help_text:
                    raise ConnectorError(f"Installed lms does not support {flag}; no command executed")
                args += [flag, self.argument(value)]
        if estimate:
            if "--estimate-only" not in help_text:
                raise ConnectorError("Installed lms does not support estimation")
            args.append("--estimate-only")
        args.append("--yes")
        return args
