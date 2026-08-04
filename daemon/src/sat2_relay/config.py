from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .credentials import CredentialStore, ResolvedSecret, resolve_secret
from .models import RelayMode, RepoRelayConfig


DEFAULT_CONFIG_DIR = Path(os.environ.get("SAT2_RELAY_HOME", Path.home() / ".sat2-relay"))
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.yml"


@dataclass(frozen=True)
class LocalConfig:
    path: Path
    host: str
    port: int
    api_token: str
    database_path: Path
    log_path: Path
    lock_path: Path
    credential_store_path: Path
    github_repository: str
    github_token_env: str
    github_alert_token_env: str
    repository_config_ref: str
    repository_config_path: str
    local_mode_override: RelayMode | None
    allow_github_writes: bool

    @property
    def credential_store(self) -> CredentialStore:
        return CredentialStore(self.credential_store_path)

    @property
    def github_secret(self) -> ResolvedSecret:
        return resolve_secret(self.credential_store, "github_token", self.github_token_env)

    @property
    def github_alert_secret(self) -> ResolvedSecret:
        direct = resolve_secret(self.credential_store, "github_alert_token", self.github_alert_token_env)
        return direct if direct.value else self.github_secret

    @property
    def github_token(self) -> str | None:
        return self.github_secret.value

    @property
    def github_alert_token(self) -> str | None:
        return self.github_alert_secret.value


def default_document() -> dict[str, Any]:
    return {
        "server": {"host": "127.0.0.1", "port": 8765, "api_token": secrets.token_urlsafe(32)},
        "storage": {
            "database": "state/state.sqlite3",
            "log": "logs/sat2-relay.jsonl",
            "lock": "state/sat2-relay.lock",
            "credentials": "credentials.bin",
        },
        "github": {
            "repository": "Upp-Ljl/sat2",
            "token_env": "SAT2_GITHUB_TOKEN",
            "alert_token_env": "SAT2_GITHUB_ALERT_TOKEN",
            "repository_config_ref": "maintenance/sat2-relay-v1",
            "repository_config_path": ".sat2/relay.yml",
            "allow_writes": False,
        },
        "relay": {"mode_override": None},
    }


def initialize_config(path: Path = DEFAULT_CONFIG_PATH, overwrite: bool = False) -> Path:
    path = path.expanduser().resolve()
    if path.exists() and not overwrite:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(default_document(), sort_keys=False, allow_unicode=True), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _resolve_relative(base: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def load_local_config(path: Path = DEFAULT_CONFIG_PATH) -> LocalConfig:
    path = path.expanduser().resolve()
    if not path.exists():
        initialize_config(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    server = raw.get("server", {})
    storage = raw.get("storage", {})
    github = raw.get("github", {})
    relay = raw.get("relay", {})
    host = str(server.get("host", "127.0.0.1"))
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("SAT2 Relay must bind to loopback only")
    port = int(server.get("port", 8765))
    if not 1024 <= port <= 65535:
        raise ValueError("server.port must be between 1024 and 65535")
    api_token = str(server.get("api_token", ""))
    if len(api_token) < 32:
        raise ValueError("server.api_token must contain at least 32 characters")
    base = path.parent
    mode_value = relay.get("mode_override")
    return LocalConfig(
        path=path,
        host=host,
        port=port,
        api_token=api_token,
        database_path=_resolve_relative(base, str(storage.get("database", "state/state.sqlite3"))),
        log_path=_resolve_relative(base, str(storage.get("log", "logs/sat2-relay.jsonl"))),
        lock_path=_resolve_relative(base, str(storage.get("lock", "state/sat2-relay.lock"))),
        credential_store_path=_resolve_relative(base, str(storage.get("credentials", "credentials.bin"))),
        github_repository=str(github.get("repository", "Upp-Ljl/sat2")),
        github_token_env=str(github.get("token_env", "SAT2_GITHUB_TOKEN")),
        github_alert_token_env=str(github.get("alert_token_env", "SAT2_GITHUB_ALERT_TOKEN")),
        repository_config_ref=str(github.get("repository_config_ref", "main")),
        repository_config_path=str(github.get("repository_config_path", ".sat2/relay.yml")),
        local_mode_override=RelayMode(mode_value) if mode_value else None,
        allow_github_writes=bool(github.get("allow_writes", False)),
    )


def parse_repository_config(text: str) -> RepoRelayConfig:
    raw = yaml.safe_load(text) or {}
    return RepoRelayConfig.model_validate(raw)
