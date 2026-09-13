import ipaddress
import os
import re
from urllib.parse import urlsplit

import httpx

from .client import ConnectorError
from .storage import atomic_json, file_lock, read_json


def validate_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ConnectorError("Use an HTTP(S) origin without credentials, path, query or fragment")
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        ip = None
    if ip and (ip.is_link_local or ip.is_unspecified or ip.is_multicast):
        raise ConnectorError("Link-local, unspecified and multicast destinations are not supported")
    if parsed.scheme == "http" and not (parsed.hostname == "localhost" or
            (ip and (ip.is_private or ip.is_loopback))):
        raise ConnectorError("Use HTTPS for remote DNS/public endpoints, or a private LAN IP")
    return url.rstrip("/")


class Connections:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.state_dir / "connections.json"

    def registry(self):
        data = read_json(self.path, {"active": "local", "profiles": {}})
        if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
            raise ConnectorError("Connection registry is malformed")
        return data

    def get(self, name=None):
        data = self.registry()
        name = name or data["active"]
        if name == "local":
            return {"name": "local", "url": self.settings.base_url, "token": self.settings.api_token, "local": True}
        if name not in data["profiles"]:
            raise ConnectorError("Unknown connection profile")
        profile = data["profiles"][name]
        env = profile.get("token_env")
        token = os.getenv(env, "") if env else ""
        if env and not token:
            raise ConnectorError(f"Token environment variable {env} is not set")
        return {"name": name, "url": validate_url(profile["url"]), "token": token, "local": False}

    async def test(self, profile):
        headers = {"Authorization": "Bearer " + profile["token"]} if profile["token"] else {}
        try:
            async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
                response = await client.get(profile["url"] + "/api/v1/models", headers=headers)
            if response.status_code in {401, 403}:
                return {"reachable": True, "compatible": False, "http_status": response.status_code, "issue": "authentication"}
            response.raise_for_status()
            data = response.json()
            valid = isinstance(data, dict) and isinstance(data.get("models"), list)
            return {"reachable": True, "compatible": valid, "http_status": response.status_code,
                    "model_count": len(data["models"]) if valid else None}
        except httpx.HTTPStatusError as e:
            return {"reachable": True, "compatible": False, "http_status": e.response.status_code}
        except (httpx.HTTPError, ValueError):
            return {"reachable": False, "compatible": False, "issue": "network_tls_or_invalid_json"}

    async def run(self, action, name="local", url=None, token_env=None):
        if action == "test":
            profile = self.get(name) if not url else {"url": validate_url(url), "token": ""}
            return {"profile": name, "url": profile["url"], **await self.test(profile)}
        if action == "select":
            profile = self.get(name)
            result = await self.test(profile)
            if not result["compatible"]:
                raise ConnectorError(f"Connection not compatible; profile unchanged ({result.get('issue', result.get('http_status'))})")
        with file_lock(self.settings.state_dir):
            data = self.registry()
            if action == "save":
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) or name == "local":
                    raise ConnectorError("Choose a profile name other than local")
                if token_env and not re.fullmatch(r"LM_(?:STUDIO|REMOTE)_[A-Z0-9_]+", token_env):
                    raise ConnectorError("Token variables must start with LM_STUDIO_ or LM_REMOTE_")
                data["profiles"][name] = {"url": validate_url(url or ""), "token_env": token_env}
            elif action == "select":
                data["active"] = name
            elif action == "delete":
                if name == data["active"]:
                    raise ConnectorError("Select another profile before deleting the active one")
                data["profiles"].pop(name, None)
            if action != "list":
                atomic_json(self.path, data)
        return {"active": data["active"], "profiles": {"local": {"url": self.settings.base_url}, **data["profiles"]},
                "note": "Remote profiles control their API only; local CLI commands are disabled while selected."}
