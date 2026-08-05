from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from .secrets import SecretStoreError, keychain_delete, keychain_get, keychain_set

WAREHOUSE_DEVICE_TOKEN_SERVICE = "com.cpym.su.lighthouse.warehouse.device"


class WarehouseFederationError(RuntimeError):
    pass


@dataclass(frozen=True)
class WarehouseFederationConfig:
    enabled: bool
    origin: str
    device_id: str
    label: str
    workspace_id: str | None

    @property
    def websocket_url(self) -> str:
        parsed = urlparse(self.origin)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}/api/lighthouse/device/v1/connect"

    def public_dict(self, *, credential_present: bool) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "origin": self.origin,
            "device_id": self.device_id or None,
            "label": self.label or None,
            "workspace_id": self.workspace_id,
            "credential_present": credential_present,
            "websocket_url": self.websocket_url if self.origin else None,
        }


def config_path() -> Path:
    return Path(
        os.environ.get("LIGHTHOUSE_CONFIG")
        or Path.home() / ".lighthouse" / "config.json"
    ).expanduser()


def _read_root() -> dict[str, Any]:
    path = config_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_warehouse_federation_config() -> WarehouseFederationConfig | None:
    value = _read_root().get("warehouse_federation")
    if not isinstance(value, dict):
        return None
    origin = str(value.get("origin") or "").strip()
    device_id = str(value.get("device_id") or "").strip()
    if not origin or not device_id:
        return None
    try:
        device_id = str(UUID(device_id))
        origin = normalize_origin(origin)
    except (ValueError, WarehouseFederationError):
        return None
    workspace_id = str(value.get("workspace_id") or "").strip() or None
    return WarehouseFederationConfig(
        enabled=bool(value.get("enabled", True)),
        origin=origin,
        device_id=device_id,
        label=str(value.get("label") or "LightHouse").strip() or "LightHouse",
        workspace_id=workspace_id,
    )


def normalize_origin(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WarehouseFederationError("Warehouse origin must be an HTTP(S) origin")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise WarehouseFederationError("Warehouse origin must not include a path or query")
    hostname = (parsed.hostname or "").lower()
    local = hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not local:
        raise WarehouseFederationError("Remote Warehouse federation requires HTTPS/WSS")
    return f"{parsed.scheme}://{parsed.netloc}"


def warehouse_device_token(config: WarehouseFederationConfig) -> str:
    return (
        keychain_get(WAREHOUSE_DEVICE_TOKEN_SERVICE, account=config.device_id) or ""
    ).strip()


def save_warehouse_federation_config(config: WarehouseFederationConfig) -> None:
    path = config_path()
    root = _read_root()
    root["warehouse_federation"] = {
        "enabled": bool(config.enabled),
        "origin": config.origin,
        "device_id": config.device_id,
        "label": config.label,
        "workspace_id": config.workspace_id,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(root, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def warehouse_instance_uuid(value: object) -> str:
    raw = str(value or "default").strip() or "default"
    try:
        return str(UUID(raw))
    except ValueError:
        return str(uuid5(NAMESPACE_URL, f"lighthouse-instance:{raw}"))


def pair_warehouse_device(
    *,
    origin: str,
    pairing_code: str,
    instance_id: str,
    label: str,
    workspace_id: str | None = None,
) -> dict[str, object]:
    normalized_origin = normalize_origin(origin)
    code = str(pairing_code or "").strip()
    if not 40 <= len(code) <= 256:
        raise WarehouseFederationError("Pairing code has an invalid length")
    normalized_instance_id = warehouse_instance_uuid(instance_id)
    response = httpx.post(
        normalized_origin + "/api/lighthouse/device/v1/enroll",
        json={
            "pairing_code": code,
            "instance_id": normalized_instance_id,
            "label": str(label or "LightHouse").strip() or "LightHouse",
        },
        timeout=20.0,
        follow_redirects=False,
    )
    if response.is_redirect:
        raise WarehouseFederationError("Warehouse enrollment redirects are not accepted")
    try:
        payload = response.json()
    except ValueError as exc:
        raise WarehouseFederationError(
            f"Warehouse enrollment returned HTTP {response.status_code} without JSON"
        ) from exc
    if response.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise WarehouseFederationError(
            str(detail or f"Warehouse enrollment failed with HTTP {response.status_code}")
        )
    if not isinstance(payload, dict):
        raise WarehouseFederationError("Warehouse enrollment response is invalid")
    device_id = str(payload.get("device_id") or "").strip()
    device_token = str(payload.get("device_token") or "").strip()
    try:
        device_id = str(UUID(device_id))
    except ValueError as exc:
        raise WarehouseFederationError("Warehouse returned an invalid device_id") from exc
    if not device_token:
        raise WarehouseFederationError("Warehouse did not return the one-time device token")
    try:
        keychain_set(
            WAREHOUSE_DEVICE_TOKEN_SERVICE,
            device_token,
            account=device_id,
        )
    except SecretStoreError as exc:
        raise WarehouseFederationError(
            "The Warehouse device token could not be stored in the operating-system credential store"
        ) from exc
    config = WarehouseFederationConfig(
        enabled=True,
        origin=normalized_origin,
        device_id=device_id,
        label=str(payload.get("label") or label or "LightHouse").strip(),
        workspace_id=str(workspace_id).strip() if workspace_id else None,
    )
    save_warehouse_federation_config(config)
    return {
        "ok": True,
        "federation": config.public_dict(credential_present=True),
        "note": "Device token stored in the operating-system credential store; plaintext was not written to config.",
    }


def disable_warehouse_federation() -> dict[str, object]:
    config = load_warehouse_federation_config()
    if config is None:
        return {"ok": True, "disabled": True, "credential_removed": False}
    removed = keychain_delete(
        WAREHOUSE_DEVICE_TOKEN_SERVICE,
        account=config.device_id,
    )
    save_warehouse_federation_config(
        WarehouseFederationConfig(
            enabled=False,
            origin=config.origin,
            device_id=config.device_id,
            label=config.label,
            workspace_id=config.workspace_id,
        )
    )
    return {"ok": True, "disabled": True, "credential_removed": removed}


def warehouse_federation_status() -> dict[str, object]:
    config = load_warehouse_federation_config()
    if config is None:
        return {"configured": False, "enabled": False, "credential_present": False}
    token_present = bool(warehouse_device_token(config))
    return {"configured": True, **config.public_dict(credential_present=token_present)}
