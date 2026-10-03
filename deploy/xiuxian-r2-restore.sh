#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ENV_FILE="${ENV_FILE:-/root/.config/restic/xiuxian-r2.env}"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/backup_engine.py" xiuxian restore "${1:-latest}"
