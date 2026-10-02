#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ENV_FILE="${ENV_FILE:-/root/.config/restic/xiuxian-r2.env}"
PROJECT_DIR="${PROJECT_DIR:-/opt/xiuxian-main}"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-/root/critical-backups/xiuxian-r2-snapshot}"
LOCK_FILE="${LOCK_FILE:-/root/critical-backups/xiuxian-r2-backup.lock}"
STOP_SERVICES="${XIUXIAN_BACKUP_STOP_SERVICES:-1}"
RETENTION_DAILY="${RETENTION_DAILY:-14}"
RETENTION_WEEKLY="${RETENTION_WEEKLY:-8}"
RETENTION_MONTHLY="${RETENTION_MONTHLY:-12}"
RESTIC_CHECK_AFTER="${RESTIC_CHECK_AFTER:-0}"
HOST_TAG="$(hostname -s 2>/dev/null || hostname)"

XIUXIAN_SERVICES=(
  xiuxian-health-observer.service
  xiuxian-safety-watchdog.service
  xiuxian.service
)

export GOMAXPROCS="${GOMAXPROCS:-2}"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

build_restic_repository() {
  if [ -z "${RESTIC_REPOSITORY:-}" ]; then
    : "${R2_ACCOUNT_ID:?missing R2_ACCOUNT_ID in $ENV_FILE}"
    R2_BUCKET="${R2_BUCKET:-mac-key}"
    R2_PREFIX="${R2_PREFIX:-xiuxian-main}"
    R2_PREFIX="${R2_PREFIX#/}"
    R2_PREFIX="${R2_PREFIX%/}"
    REPO_PATH="$R2_BUCKET"
    if [ -n "$R2_PREFIX" ]; then
      REPO_PATH="${REPO_PATH}/${R2_PREFIX}"
    fi
    export RESTIC_REPOSITORY="s3:https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com/${REPO_PATH}"
  fi
}

[ -r "$ENV_FILE" ] || die "missing env file: $ENV_FILE"
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${AWS_ACCESS_KEY_ID:?missing AWS_ACCESS_KEY_ID in $ENV_FILE}"
: "${AWS_SECRET_ACCESS_KEY:?missing AWS_SECRET_ACCESS_KEY in $ENV_FILE}"
if [ -z "${RESTIC_PASSWORD:-}" ] && [ -z "${RESTIC_PASSWORD_FILE:-}" ]; then
  die "missing RESTIC_PASSWORD or RESTIC_PASSWORD_FILE in $ENV_FILE"
fi
build_restic_repository

export AWS_REGION="${AWS_REGION:-auto}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-$AWS_REGION}"

[ -d "$PROJECT_DIR" ] || die "missing Xiuxian project dir: $PROJECT_DIR"
command -v restic >/dev/null 2>&1 || die "restic not found"
command -v rsync >/dev/null 2>&1 || die "rsync not found"
command -v systemctl >/dev/null 2>&1 || die "systemctl not found"
[ -x "$PROJECT_DIR/.venv/bin/python" ] || die "project Python not found"
[ -f "$PROJECT_DIR/tools/snapshot_sqlite_db.py" ] || die "SQLite snapshot helper not found"

mkdir -p "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another xiuxian-r2-backup run is active; exiting"
  exit 0
fi

STOPPED_SERVICES=()

service_exists() {
  systemctl cat "$1" >/dev/null 2>&1
}

stop_services_if_needed() {
  [ "$STOP_SERVICES" = "1" ] || return 0
  for service in "${XIUXIAN_SERVICES[@]}"; do
    service_exists "$service" || continue
    if systemctl is-active --quiet "$service"; then
      log "stop $service for a consistent snapshot"
      systemctl stop "$service"
      STOPPED_SERVICES+=("$service")
    fi
  done
}

start_stopped_services() {
  [ "${#STOPPED_SERVICES[@]}" -gt 0 ] || return 0
  for ((idx=${#STOPPED_SERVICES[@]}-1; idx>=0; idx--)); do
    service="${STOPPED_SERVICES[$idx]}"
    log "start $service"
    systemctl start "$service" || true
  done
  STOPPED_SERVICES=()
}

cleanup() {
  status=$?
  start_stopped_services || true
  exit "$status"
}
trap cleanup EXIT

log "prepare snapshot: $SNAPSHOT_DIR"
mkdir -p "$SNAPSHOT_DIR"

stop_services_if_needed

log "rsync project tree"
mkdir -p "$SNAPSHOT_DIR/project"
rsync -a --delete \
  --exclude '/.venv/' \
  --exclude '/__pycache__/' \
  --exclude '/.pytest_cache/' \
  --exclude '/.ruff_cache/' \
  --exclude '*/__pycache__/' \
  --exclude '/data/state/chaogu_state.db' \
  --exclude '/data/state/chaogu_state.db-wal' \
  --exclude '/data/state/chaogu_state.db-shm' \
  --exclude '/data/state/chaogu_state.db-journal' \
  "$PROJECT_DIR/" "$SNAPSHOT_DIR/project/"

log "snapshot SQLite state with committed WAL data"
"$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/tools/snapshot_sqlite_db.py" \
  "$PROJECT_DIR/data/state/chaogu_state.db" \
  "$SNAPSHOT_DIR/project/data/state/chaogu_state.db"

start_stopped_services

log "capture systemd units and restore helpers"
mkdir -p "$SNAPSHOT_DIR/systemd" "$SNAPSHOT_DIR/root/scripts"
for service in "${XIUXIAN_SERVICES[@]}"; do
  service_exists "$service" || continue
  systemctl cat "$service" >"$SNAPSHOT_DIR/systemd/${service}.cat"
  if [ -f "/etc/systemd/system/$service" ]; then
    cp -a "/etc/systemd/system/$service" "$SNAPSHOT_DIR/systemd/"
  fi
done
cp -a /root/scripts/xiuxian-r2-backup.sh "$SNAPSHOT_DIR/root/scripts/"
if [ -f /root/scripts/xiuxian-r2-restore.sh ]; then
  cp -a /root/scripts/xiuxian-r2-restore.sh "$SNAPSHOT_DIR/root/scripts/"
fi

log "capture python package freeze"
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  "$PROJECT_DIR/.venv/bin/python" -m pip freeze >"$SNAPSHOT_DIR/requirements-freeze.txt" || true
fi

log "write restore manifest"
{
  printf 'created_at=%s\n' "$(date -Is)"
  printf 'host=%s\n' "$HOST_TAG"
  printf 'project_dir=%s\n' "$PROJECT_DIR"
  printf 'snapshot_dir=%s\n' "$SNAPSHOT_DIR"
  printf 'git_branch=%s\n' "$(git -C "$PROJECT_DIR" branch --show-current 2>/dev/null || true)"
  printf 'git_commit=%s\n' "$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null || true)"
  printf 'git_remote=%s\n' "$(git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null || true)"
  printf 'python=%s\n' "$("$PROJECT_DIR/.venv/bin/python" --version 2>/dev/null || true)"
  printf '\n[git_status]\n'
  git -C "$PROJECT_DIR" status --short 2>/dev/null || true
  printf '\n[services]\n'
  for service in "${XIUXIAN_SERVICES[@]}"; do
    service_exists "$service" || continue
    printf '%s enabled=%s active=%s\n' \
      "$service" \
      "$(systemctl is-enabled "$service" 2>/dev/null || true)" \
      "$(systemctl is-active "$service" 2>/dev/null || true)"
  done
} >"$SNAPSHOT_DIR/MANIFEST.txt"
date -Is >"$SNAPSHOT_DIR/.snapshot-created-at"

if ! restic snapshots >/dev/null 2>&1; then
  log "initialize restic repository"
  restic init
fi

log "backup snapshot to restic repository"
restic backup "$SNAPSHOT_DIR" \
  --host "$HOST_TAG" \
  --tag xiuxian \
  --tag r2 \
  --tag xiuxian-main \
  --exclude-caches

log "apply retention policy"
restic forget \
  --host "$HOST_TAG" \
  --tag xiuxian \
  --keep-daily "$RETENTION_DAILY" \
  --keep-weekly "$RETENTION_WEEKLY" \
  --keep-monthly "$RETENTION_MONTHLY" \
  --prune

if [ "$RESTIC_CHECK_AFTER" = "1" ]; then
  log "run restic check"
  restic check
fi

log "xiuxian R2 backup complete"
