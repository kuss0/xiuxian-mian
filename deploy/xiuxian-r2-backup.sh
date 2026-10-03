#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ENV_FILE="${ENV_FILE:-/root/.config/restic/xiuxian-r2.env}"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ACTION=backup
case "${1:-}" in
  '') ;;
  --check-config) ACTION=check-config ;;
  --recover) ACTION=recover ;;
  *) echo 'Usage: xiuxian-r2-backup.sh [--check-config|--recover]' >&2; exit 2 ;;
esac
exec python3 "$SCRIPT_DIR/backup_engine.py" xiuxian "$ACTION"
