#!/usr/bin/env bash
set -euo pipefail
ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/sat2-relay"
VENV="$ROOT/venv"
CONFIG="$ROOT/config.yml"
SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT" "$HOME/.config/systemd/user"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install "$SOURCE"
[[ -f "$CONFIG" ]] || "$VENV/bin/sat2-relay" --config "$CONFIG" init
cat > "$HOME/.config/systemd/user/sat2-relay.service" <<EOF
[Unit]
Description=SAT2 Relay 2
After=network-online.target
[Service]
ExecStart=$VENV/bin/sat2-relay --config $CONFIG supervise
Restart=always
RestartSec=3
[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now sat2-relay.service
printf 'Installed. Store the token with:\n  %s --config %s credentials set --github-token\n' "$VENV/bin/sat2-relay" "$CONFIG"
