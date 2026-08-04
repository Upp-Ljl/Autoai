from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import uvicorn

from .api import create_app
from .config import DEFAULT_CONFIG_PATH, LocalConfig, initialize_config, load_local_config
from .credentials import prompt_secret
from .db import RelayDB
from .github import GitHubClient
from .lock import ProcessLock
from .logging_utils import configure_logging
from .service import RelayService


def build_clients(local: LocalConfig) -> tuple[GitHubClient, GitHubClient]:
    primary = local.github_secret
    alert = local.github_alert_secret
    github = GitHubClient(primary.value, token_source=primary.source, token_fingerprint=primary.fingerprint)
    if alert.value == primary.value:
        return github, github
    return github, GitHubClient(alert.value, token_source=alert.source, token_fingerprint=alert.fingerprint)


def build_runtime(config_path: Path) -> tuple[LocalConfig, RelayDB, RelayService]:
    local = load_local_config(config_path)
    db = RelayDB(local.database_path)
    github, alert = build_clients(local)
    service = RelayService(local, db, github, alert)
    return local, db, service


def _serve(local: LocalConfig, db: RelayDB, service: RelayService) -> int:
    configure_logging(local.log_path, os.environ.get("SAT2_RELAY_LOG_LEVEL", "INFO"))
    app = create_app(local, db, service)
    uvicorn.run(app, host=local.host, port=local.port, log_level="info")
    return 0


def _supervise(config_path: Path, local: LocalConfig) -> int:
    configure_logging(local.log_path, os.environ.get("SAT2_RELAY_LOG_LEVEL", "INFO"))
    log = logging.getLogger("sat2_relay.supervisor")
    stop = False

    def handle_stop(_signum, _frame):
        nonlocal stop
        stop = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_stop)

    with ProcessLock(local.lock_path):
        while not stop:
            env = os.environ.copy()
            env["SAT2_RELAY_CHILD"] = "1"
            command = [sys.executable, "-m", "sat2_relay", "--config", str(config_path), "serve"]
            log.info("starting relay child")
            child = subprocess.Popen(command, env=env)
            while child.poll() is None and not stop:
                time.sleep(0.5)
            if stop:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                break
            log.error("relay child exited with code %s; restarting in 3 seconds", child.returncode)
            time.sleep(3)
    return 0


def _print(value, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    elif isinstance(value, dict):
        for key, item in value.items():
            print(f"{key}: {item}")
    else:
        print(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sat2-relay")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--overwrite", action="store_true")

    sub.add_parser("serve")
    sub.add_parser("supervise")
    sub.add_parser("poll-once")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--shallow", action="store_true")
    doctor.add_argument("--json", action="store_true")

    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")

    credentials = sub.add_parser("credentials")
    credentials_sub = credentials.add_subparsers(dest="credentials_command", required=True)
    cred_set = credentials_sub.add_parser("set")
    cred_set.add_argument("--github-token", action="store_true")
    cred_set.add_argument("--alert-token", action="store_true")
    cred_set.add_argument("--value", help="Unsafe for shell history; interactive prompt is recommended.")
    credentials_sub.add_parser("show")
    cred_clear = credentials_sub.add_parser("clear")
    cred_clear.add_argument("--github-token", action="store_true")
    cred_clear.add_argument("--alert-token", action="store_true")
    cred_clear.add_argument("--all", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "init":
        path = initialize_config(args.config, args.overwrite)
        print(path)
        return 0

    local = load_local_config(args.config)

    if args.command == "credentials":
        store = local.credential_store
        if args.credentials_command == "show":
            _print(store.summary(), as_json=True)
            return 0
        if args.credentials_command == "set":
            if not args.github_token and not args.alert_token:
                args.github_token = True
            value = args.value or prompt_secret("GitHub token")
            if args.github_token:
                store.set("github_token", value)
            if args.alert_token:
                store.set("github_alert_token", value)
            _print(store.summary(), as_json=True)
            return 0
        if args.credentials_command == "clear":
            if args.all or (not args.github_token and not args.alert_token):
                store.clear()
            else:
                if args.github_token:
                    store.clear("github_token")
                if args.alert_token:
                    store.clear("github_alert_token")
            _print(store.summary(), as_json=True)
            return 0

    if args.command == "supervise":
        return _supervise(args.config.resolve(), local)

    local, db, service = build_runtime(args.config)

    if args.command == "poll-once":
        _print(service.poll_once(), as_json=True)
        return 0
    if args.command == "doctor":
        result = service.doctor(deep=not args.shallow)
        _print(result, as_json=args.json)
        return 0 if result["ok"] else 2
    if args.command == "status":
        _print(db.status_snapshot(), as_json=args.json)
        return 0
    if args.command == "serve":
        if os.environ.get("SAT2_RELAY_CHILD") == "1":
            return _serve(local, db, service)
        with ProcessLock(local.lock_path):
            return _serve(local, db, service)
    return 2


if __name__ == "__main__":
    sys.exit(main())
