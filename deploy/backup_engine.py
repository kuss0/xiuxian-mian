#!/usr/bin/env python3
"""Application snapshots with bounded recovery and instance-scoped restic retention."""
import argparse
import contextlib
import datetime as dt
import fcntl
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid

SQLITE_HEADER = b'SQLite format 3\x00'
SIDECARS = ('-wal', '-shm', '-journal')
SERVICES = ('xiuxian-health-observer.service', 'xiuxian-safety-watchdog.service',
            'xiuxian-listener.service', 'xiuxian.service')
CHILD = None


class BackupError(RuntimeError):
    pass


def log(message):
    print(f'[{dt.datetime.now().astimezone().isoformat(timespec="seconds")}] {message}', flush=True)


def positive(name, default):
    value = float(os.environ.get(name, default))
    if not 0 < value < 10**7:
        raise BackupError(f'{name} must be a finite positive number')
    return value


def flag(name, default=False):
    value = os.environ.get(name, '1' if default else '0').lower()
    if value not in ('0', '1', 'true', 'false'):
        raise BackupError(f'{name} must be 0 or 1')
    return value in ('1', 'true')


def atomic_json(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def run(args, seconds=120, allowed=(0,), capture=False):
    global CHILD
    proc = subprocess.Popen([str(x) for x in args], start_new_session=True,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None, text=True)
    CHILD = proc
    try:
        out, err = proc.communicate(timeout=seconds)
        if proc.returncode not in allowed:
            detail = (err or out or '').strip()[-1200:]
            raise BackupError(f'{args[0]} {args[1] if len(args)>1 else ""} failed ({proc.returncode}): {detail}')
        return proc.returncode, out or ''
    except BaseException:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        raise
    finally:
        CHILD = None


def real_directory(path, exists=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise BackupError(f'Absolute path without .. required: {path}')
    if path != path.resolve():
        raise BackupError(f'Symlinked path is not permitted: {path}')
    if exists and not path.is_dir():
        raise BackupError(f'Missing source directory: {path}')
    return path


def overlaps(a, b):
    return a == b or a in b.parents or b in a.parents


class Config:
    def __init__(self, kind, action='backup'):
        self.kind = kind
        self.root = real_directory(os.environ.get('BACKUP_ROOT', '/root/critical-backups'))
        if self.root in map(Path, ('/', '/root', '/etc', '/opt', '/usr', '/var', '/tmp', '/home')):
            raise BackupError('BACKUP_ROOT must be a dedicated backup directory')
        self.target = real_directory(os.environ.get('SNAPSHOT_DIR', str(self.root / f'{kind}-r2-snapshot-v2')))
        if self.target.parent != self.root or self.target.name.startswith('.'):
            raise BackupError('SNAPSHOT_DIR must be a direct, non-hidden child of BACKUP_ROOT')
        self.instance = os.environ.get('BACKUP_INSTANCE_ID', '')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{5,95}', self.instance):
            raise BackupError('Set a stable, unique BACKUP_INSTANCE_ID (6-96 letters/digits/._-)')
        self.tag = 'instance:' + self.instance
        self.host = os.environ.get('HOST_TAG') or os.uname().nodename.split('.')[0]
        self.copy_timeout = positive('COPY_TIMEOUT_SEC', 600)
        self.restic_timeout = positive('RESTIC_TIMEOUT_SEC', 3600)
        self.recovery_timeout = positive('RECOVERY_TIMEOUT_SEC', 180)
        self.command_timeout = positive('SERVICE_COMMAND_TIMEOUT_SEC', 45)
        self.ready_stable = positive('READY_STABLE_SEC', 3)
        self.stamp = dt.datetime.now().astimezone().isoformat(timespec='seconds')
        self.sources = []
        self.excludes = []
        require_source = action in ('backup', 'archive', 'check-config')
        if kind == 'vaultwarden':
            self.data = real_directory(os.environ.get('DATA_DIR', '/opt/vaultwarden/vw-data'), require_source)
            self.compose = Path(os.environ.get('COMPOSE_FILE', '/opt/vaultwarden/docker-compose.yml'))
            self.service = os.environ.get('SERVICE_NAME', 'vaultwarden')
            self.stop = flag('STOP_CONTAINER', flag('VW_BACKUP_STOP_CONTAINER', True))
            self.sources = [self.data]
            self.required = self.data / 'db.sqlite3'
        elif kind == 'xiuxian':
            self.data = real_directory(os.environ.get('PROJECT_DIR', '/opt/xiuxian-main'), require_source)
            if require_source and not os.access(self.data/'.venv/bin/python', os.X_OK):
                raise BackupError('Xiuxian project Python is missing or not executable')
            self.stop = flag('STOP_SERVICES', flag('XIUXIAN_BACKUP_STOP_SERVICES', True))
            self.sources = [self.data]
            self.required = self.data / 'data/state/chaogu_state.db'
            self.excludes = ['.venv/', '__pycache__/', '.pytest_cache/', '.ruff_cache/']
        else:
            self.stop = False
            self.required = None
            self.paths_file = Path(os.environ.get('PATHS_FILE', '/root/.config/restic/critical-paths.txt'))
            self.exclude_file = Path(os.environ.get('EXCLUDES_FILE', '/root/.config/restic/core-excludes.txt'))
            self.excludes = self.read_paths(self.exclude_file)
            for entry in self.read_paths(self.paths_file):
                path = Path(entry)
                if not path.is_absolute() or '..' in path.parts:
                    raise BackupError(f'Invalid configured source: {path}')
                if require_source and not path.exists():
                    raise BackupError(f'Missing configured source: {path}; correct PATHS_FILE first')
                if not self.excluded(path, path, self.excludes):
                    self.sources.append(path)
        for source in self.sources:
            if overlaps(source.resolve(), self.root) or overlaps(source.resolve(), self.target):
                raise BackupError(f'Backup destination overlaps source: {source}')
        if not self.sources:
            raise BackupError('No backup sources configured')
        token = hashlib.sha256(str(self.target).encode()).hexdigest()[:16]
        self.lock = self.root / f'.backup-{token}.lock'
        self.recovery = self.root / f'.recovery-{token}.json'
        self.status = self.root / f'.status-{kind}-{self.instance}.json'

    @staticmethod
    def read_paths(path):
        return [line.strip() for line in path.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith('#')]

    @staticmethod
    def excluded(path, root, patterns):
        rel = str(path.relative_to(root)) if path != root else ''
        for pattern in patterns:
            if pattern.startswith('/'):
                if fnmatch.fnmatch(str(path), pattern.rstrip('/')):
                    return True
            elif fnmatch.fnmatch(rel, pattern.rstrip('/')) or fnmatch.fnmatch(path.name, pattern.rstrip('/')):
                return True
        return False

    def owner(self):
        return {'version': 2, 'kind': self.kind, 'instance': self.instance,
                'target': str(self.target), 'sources': [str(x) for x in self.sources]}

    def prepare(self, check_owner=True):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if check_owner and self.target.exists():
            marker = self.target / '.backup-owner.json'
            if marker.is_symlink() or not marker.is_file() or json.loads(marker.read_text()) != self.owner():
                raise BackupError('Existing snapshot is not owned by this job; use a new SNAPSHOT_DIR')


def sqlite_snapshot(source, destination, deadline):
    if source.is_symlink() or destination.is_symlink() or destination.parent != destination.parent.resolve():
        raise BackupError('SQLite snapshot paths must not traverse symlinks')
    if destination.exists():
        raise BackupError('SQLite destination must be a fresh file')
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    def check(*_):
        if time.monotonic() >= deadline:
            raise BackupError('SQLite snapshot exceeded copy deadline')
    with contextlib.closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=2)) as reader:
        with contextlib.closing(sqlite3.connect(destination)) as writer:
            reader.backup(writer, pages=256, progress=check, sleep=0.05)
            writer.execute('PRAGMA journal_mode=DELETE')
            writer.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            if writer.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise BackupError(f'SQLite quick_check failed: {source.name}')
    check()
    metadata = source.stat()
    shutil.copystat(source, destination, follow_symlinks=False)
    if os.geteuid() == 0:
        os.chown(destination, metadata.st_uid, metadata.st_gid)


def validate_db(path):
    if path.is_symlink() or not path.is_file():
        raise BackupError(f'Missing or symlinked required database: {path}')
    with path.open('rb') as stream:
        if stream.read(16) != SQLITE_HEADER:
            raise BackupError(f'Missing, empty or invalid SQLite database: {path}')


def is_sqlite_file(path):
    # Do not block on FIFOs or follow links while inspecting file headers.
    if not stat.S_ISREG(path.lstat().st_mode):
        return False
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        return stat.S_ISREG(os.fstat(stream.fileno()).st_mode) and stream.read(16) == SQLITE_HEADER


def literal_filter(relative, directory=False):
    # rsync filters are patterns, even when read from an exclude file.
    escaped = ''.join('\\'+c if c in '\\*?[' else c for c in str(relative))
    return '/'+escaped+('/' if directory else '')


def snapshot_tree(source, destination, patterns, deadline):
    """Copy fresh files, then snapshot each SQLite DB; never delete source/destination files."""
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if source.is_file() and not source.is_symlink():
        if source.name in ('PG_VERSION', 'ibdata1'):
            raise BackupError(f'Database requires an application-specific dump: {source}')
        if is_sqlite_file(source):
            sqlite_snapshot(source, destination, deadline)
        else:
            run(['rsync', '-a', '--', source, destination], max(0.1, deadline-time.monotonic()))
        return
    if source.is_symlink():
        destination.symlink_to(os.readlink(source))
        return
    database_files, excludes = [], []
    for folder, dirs, files in os.walk(source, followlinks=False):
        if time.monotonic() >= deadline:
            raise BackupError('Directory scan exceeded copy deadline')
        parent = Path(folder)
        for name in dirs[:]:
            item = parent/name
            if Config.excluded(item, source, patterns):
                excludes.append(literal_filter(item.relative_to(source), directory=True))
                dirs.remove(name)
        for name in files:
            item = parent/name
            rel = str(item.relative_to(source))
            if Config.excluded(item, source, patterns):
                excludes.append(literal_filter(rel))
                continue
            if name in ('PG_VERSION', 'ibdata1'):
                raise BackupError(f'Database requires an application-specific dump: {item}')
            if item.is_symlink():
                continue
            sidecar_of_sqlite = False
            for suffix in SIDECARS:
                if name.endswith(suffix):
                    with contextlib.suppress(FileNotFoundError):
                        sidecar_of_sqlite = is_sqlite_file(parent/name[:-len(suffix)])
                    break
            if sidecar_of_sqlite:
                excludes.append(literal_filter(rel))
                continue
            try:
                if is_sqlite_file(item):
                    database_files.append((item, destination/rel))
                    excludes.extend(literal_filter(rel+suffix) for suffix in ('', *SIDECARS))
            except FileNotFoundError:
                raise BackupError(f'Source changed during scan: {item}')
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    # NUL delimiters handle newlines in names; a file avoids argv size limits.
    with tempfile.NamedTemporaryFile(prefix='.backup-excludes-', dir=destination.parent) as filters:
        filters.write(b''.join(os.fsencode(pattern)+b'\0' for pattern in excludes))
        filters.flush()
        run(['rsync', '-a', '--from0', '--exclude-from', filters.name, '--',
             str(source)+'/', str(destination)+'/'], max(0.1, deadline-time.monotonic()))
    # A database can appear after the source scan. Never publish its raw copy.
    for folder, _, files in os.walk(destination, followlinks=False):
        if time.monotonic() >= deadline:
            raise BackupError('Copied directory scan exceeded copy deadline')
        for name in files:
            copied = Path(folder)/name
            if copied.is_symlink() or not copied.exists():
                continue
            if name in ('PG_VERSION', 'ibdata1'):
                raise BackupError(f'Database requires an application-specific dump: {copied}')
            if is_sqlite_file(copied):
                original = source/copied.relative_to(destination)
                copied.unlink()
                for suffix in SIDECARS:
                    Path(str(copied)+suffix).unlink(missing_ok=True)
                database_files.append((original, copied))
    for source_db, dest_db in database_files:
        sqlite_snapshot(source_db, dest_db, deadline)


class Applications:
    def __init__(self, cfg):
        self.cfg = cfg
        self.pending = []

    def save(self):
        if self.pending:
            atomic_json(self.cfg.recovery, {'owner': self.cfg.owner(), 'pending': self.pending})
        else:
            self.cfg.recovery.unlink(missing_ok=True)

    def load(self):
        if self.cfg.recovery.exists():
            data = json.loads(self.cfg.recovery.read_text())
            if data['owner'] != self.cfg.owner():
                raise BackupError('Recovery journal belongs to another configuration')
            self.pending = data['pending']
            if self.cfg.kind == 'xiuxian' and any(x not in SERVICES for x in self.pending):
                raise BackupError('Unexpected service in recovery journal')
            if self.cfg.kind == 'vaultwarden' and any(not re.fullmatch(r'[0-9a-f]{12,64}', x) for x in self.pending):
                raise BackupError('Unexpected container ID in recovery journal')

    def docker_state(self, container, seconds):
        _, out = run(['docker', 'inspect', '--format', '{{json .State}}', container], seconds, capture=True)
        return json.loads(out)

    def stop(self):
        cfg = self.cfg
        if not cfg.stop:
            return
        if cfg.kind == 'vaultwarden':
            if not cfg.compose.is_file():
                raise BackupError('Compose file does not exist')
            _, out = run(['docker', 'compose', '-f', cfg.compose, 'ps', '-a', '-q', cfg.service], cfg.command_timeout, capture=True)
            containers = out.split()
            if len(containers) != 1 or not re.fullmatch(r'[0-9a-f]{12,64}', containers[0]):
                raise BackupError('Expected exactly one Compose container')
            container = containers[0]
            if self.docker_state(container, cfg.command_timeout).get('Running'):
                self.pending.append(container)
                self.save()  # Record intent BEFORE a stop that can partially succeed.
                log('stop Vaultwarden for snapshot')
                run(['docker', 'stop', '--time', '30', container], cfg.command_timeout)
        elif cfg.kind == 'xiuxian':
            for service in SERVICES:
                code, _ = run(['systemctl', 'is-active', '--quiet', service], cfg.command_timeout, (0, 3, 4), True)
                if code == 0:
                    self.pending.append(service)
                    self.save()
                    log('stop '+service+' for snapshot')
                    run(['systemctl', 'stop', service], cfg.command_timeout)

    def resume(self):
        if not self.pending:
            return
        cfg = self.cfg
        deadline = time.monotonic()+cfg.recovery_timeout
        failures = []
        for item in reversed(self.pending.copy()):
            try:
                remaining = max(0.1, deadline-time.monotonic())
                command = ['docker', 'start', item] if cfg.kind == 'vaultwarden' else ['systemctl', 'start', item]
                run(command, min(cfg.command_timeout, remaining))
                stable_since = None
                while time.monotonic() < deadline:
                    if cfg.kind == 'vaultwarden':
                        state = self.docker_state(item, min(cfg.command_timeout, max(0.1, deadline-time.monotonic())))
                        ready = state.get('Running') and state.get('Health', {}).get('Status', 'healthy') == 'healthy'
                    else:
                        code, _ = run(['systemctl', 'is-active', '--quiet', item], min(cfg.command_timeout, max(0.1, deadline-time.monotonic())), (0, 3, 4), True)
                        ready = code == 0
                    stable_since = (stable_since or time.monotonic()) if ready else None
                    if stable_since and time.monotonic()-stable_since >= cfg.ready_stable:
                        break
                    time.sleep(min(1, cfg.ready_stable))
                else:
                    raise BackupError('readiness deadline exceeded')
                self.pending.remove(item)
                self.save()
                log('recovered '+item)
            except Exception as exc:
                failures.append(f'{item}: {exc}')
        if failures:
            raise BackupError('Application recovery failed; journal retained: '+'; '.join(failures))


def repository(cfg):
    if not os.environ.get('RESTIC_REPOSITORY'):
        values = [os.environ.get(k, '') for k in ('R2_ACCOUNT_ID', 'R2_BUCKET', 'R2_PREFIX')]
        if not all(values) or '..' in values[2].split('/'):
            raise BackupError('Set R2_ACCOUNT_ID, R2_BUCKET and a dedicated R2_PREFIX')
        account, bucket, prefix = values
        os.environ['RESTIC_REPOSITORY'] = f's3:https://{account}.r2.cloudflarestorage.com/{bucket}/{prefix.strip("/")}'
    if not any(os.environ.get(k) for k in ('RESTIC_PASSWORD', 'RESTIC_PASSWORD_FILE', 'RESTIC_PASSWORD_COMMAND')):
        raise BackupError('Missing restic password configuration')
    os.environ.setdefault('AWS_REGION', 'auto')


def ensure_repo(cfg):
    code, _ = run(['restic', 'cat', 'config'], min(300, cfg.restic_timeout), (0, 10), True)
    if code == 10:
        if not flag('RESTIC_AUTO_INIT', False):
            raise BackupError('Repository missing; set RESTIC_AUTO_INIT=1 only for a new repository')
        run(['restic', 'init'], min(300, cfg.restic_timeout))


def snapshot(cfg):
    cfg.prepare()
    if cfg.required:
        validate_db(cfg.required)
    stage = Path(tempfile.mkdtemp(prefix=f'.{cfg.kind}-stage-', dir=cfg.root))
    atomic_json(stage/'.backup-owner.json', cfg.owner())
    apps = Applications(cfg)
    try:
        apps.stop()
        deadline = time.monotonic()+cfg.copy_timeout
        if cfg.kind == 'vaultwarden':
            snapshot_tree(cfg.data, stage, cfg.excludes, deadline)
        elif cfg.kind == 'xiuxian':
            snapshot_tree(cfg.data, stage/'project', cfg.excludes, deadline)
            units = stage/'systemd'
            units.mkdir()
            for service in SERVICES:
                src = Path('/etc/systemd/system')/service
                if src.is_file():
                    shutil.copy2(src, units/service)
                drops = Path(str(src)+'.d')
                if drops.is_dir():
                    shutil.copytree(drops, units/(service+'.d'))
            python = cfg.data/'.venv/bin/python'
            _, frozen = run([python, '-m', 'pip', 'freeze'], min(60, max(0.1, deadline-time.monotonic())), capture=True)
            (stage/'requirements-freeze.txt').write_text(frozen)
            helpers = stage/'root/scripts'
            helpers.mkdir(parents=True)
            for name in ('backup_engine.py', 'xiuxian-r2-backup.sh', 'xiuxian-r2-restore.sh'):
                helper = Path(__file__).resolve().parent/name
                if helper.is_file():
                    shutil.copy2(helper, helpers/name)
        else:
            for source in cfg.sources:
                snapshot_tree(source, stage/'rootfs'/str(source).lstrip('/'), cfg.excludes, deadline)
            if flag('CAPTURE_DOCKER', False):
                _, ids = run(['docker', 'ps', '-aq'], 30, capture=True)
                if ids.split():
                    _, metadata = run(['docker', 'inspect', *ids.split()], 30, capture=True)
                    (stage/'docker-containers.json').write_text(metadata)
                _, volumes = run(['docker', 'volume', 'ls', '-q'], 30, capture=True)
                for volume in volumes.split():
                    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', volume):
                        raise BackupError('Unexpected Docker volume name')
                    source = Path('/var/lib/docker/volumes')/volume/'_data'
                    snapshot_tree(source, stage/'docker-volumes'/volume, [], deadline)
        if cfg.required:
            validate_db(stage/'db.sqlite3' if cfg.kind == 'vaultwarden'
                        else stage/'project/data/state/chaogu_state.db')
        apps.resume()
        atomic_json(stage/'.backup-owner.json', cfg.owner())
        atomic_json(stage/'MANIFEST.json', {**cfg.owner(), 'created_at': cfg.stamp, 'host': cfg.host})
        stamp_file = stage/'.snapshot-created-at'
        stamp_file.unlink(missing_ok=True)
        stamp_file.write_text(cfg.stamp+'\n')
        previous = None
        if cfg.target.exists():
            cfg.prepare()  # Re-check ownership immediately before replacing it.
            previous = cfg.root/f'.{cfg.kind}-previous-{uuid.uuid4().hex}'
            cfg.target.rename(previous)
        try:
            stage.rename(cfg.target)
        except BaseException:
            if previous:
                previous.rename(cfg.target)
            raise
        if previous:
            shutil.rmtree(previous)
        return cfg.target
    finally:
        try:
            apps.resume()
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def retention(cfg):
    command = ['restic', 'forget', '--tag', f'{cfg.kind},{cfg.tag}', '--path', str(cfg.target),
               '--group-by', 'host,paths,tags']
    counts = []
    for period, default in [('DAILY', 14), ('WEEKLY', 8), ('MONTHLY', 12)]:
        count = int(os.environ.get('RETENTION_'+period, default))
        if count < 0:
            raise BackupError('Retention counts must not be negative')
        counts.append(count)
        command += ['--keep-'+period.lower(), str(count)]
    if not any(counts):
        raise BackupError('At least one retention count must be positive')
    command += ['--prune']
    run(command, cfg.restic_timeout)


def restore(cfg, requested):
    _, out = run(['restic', 'snapshots', '--json'], min(300, cfg.restic_timeout), capture=True)
    snapshots = [s for s in json.loads(out) if cfg.kind in s.get('tags', [])]
    if requested != 'latest':
        selected = [s for s in snapshots if s['id'].startswith(requested)]
        if len(selected) != 1:
            raise BackupError('Snapshot ID must identify exactly one snapshot of this application')
    else:
        instance = os.environ.get('RESTORE_INSTANCE_ID', cfg.instance)
        selected = [s for s in snapshots if 'instance:'+instance in s.get('tags', [])]
        if not selected and os.environ.get('RESTORE_HOST'):
            selected = [s for s in snapshots if s.get('hostname') == os.environ['RESTORE_HOST']
                        and not any(tag.startswith('instance:') for tag in s.get('tags', []))]
        if not selected:
            raise BackupError('No snapshot for this instance; specify an exact legacy snapshot ID or RESTORE_HOST')
        selected = [max(selected, key=lambda s:s['time'])]
    chosen = selected[0]
    if len(chosen.get('paths', [])) != 1:
        raise BackupError('Use restic explicitly to restore legacy multi-root snapshots')
    source = Path(chosen['paths'][0])
    if not source.is_absolute() or '..' in source.parts:
        raise BackupError('Invalid snapshot root path')
    target = Path(tempfile.mkdtemp(prefix=cfg.kind+'-restore-', dir=cfg.root))
    run(['restic', 'restore', chosen['id'], '--target', target, '--verify'], cfg.restic_timeout)
    payload = target/str(source).lstrip('/')
    db = payload/'db.sqlite3' if cfg.kind == 'vaultwarden' else payload/'project/data/state/chaogu_state.db'
    if cfg.kind in ('vaultwarden', 'xiuxian'):
        validate_db(db)
        with contextlib.closing(sqlite3.connect(db.as_uri()+'?mode=ro', uri=True, timeout=2)) as conn:
            deadline = time.monotonic()+60
            conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise BackupError('Restored database quick_check failed')
    log(f'Restored verified snapshot {chosen["id"][:8]} to {payload}')


def alert(cfg, message):
    payload = {'status': 'failed', 'time': dt.datetime.now().astimezone().isoformat(),
               'kind': cfg.kind, 'instance': cfg.instance, 'error': message}
    atomic_json(cfg.status, payload)
    log('ERROR: '+message)
    with contextlib.suppress(Exception):
        run(['logger', '-p', 'user.err', '-t', 'backup-failure', f'{cfg.kind}/{cfg.instance}: {message}'], 5)
    hook = os.environ.get('BACKUP_ALERT_PROGRAM')
    if hook:
        if not Path(hook).is_absolute():
            raise BackupError('BACKUP_ALERT_PROGRAM must be an absolute executable path')
        with contextlib.suppress(Exception):
            run([hook, str(cfg.status)], 15)


def success(cfg):
    payload = {'status': 'success', 'time': dt.datetime.now().astimezone().isoformat(),
               'kind': cfg.kind, 'instance': cfg.instance}
    atomic_json(cfg.status, payload)
    persistent = Path(os.environ.get('BACKUP_ALERT_DIR', '/var/lib/backup-alerts'))/(cfg.kind+'.json')
    if persistent.is_file() and json.loads(persistent.read_text()).get('instance') == cfg.instance:
        atomic_json(persistent, {**payload, 'status': 'recovered'})


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('vaultwarden', 'xiuxian', 'critical'))
    parser.add_argument('action', choices=('backup', 'archive', 'restore', 'recover', 'check-config', 'restic', 'alert'))
    parser.add_argument('extra', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    def interrupted(signum, _frame):
        raise BackupError(f'Interrupted by signal {signum}')
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, interrupted)
    if args.action == 'alert':
        from types import SimpleNamespace
        directory = Path(os.environ.get('BACKUP_ALERT_DIR', '/var/lib/backup-alerts'))
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        context = SimpleNamespace(kind=args.kind, instance=os.environ.get('BACKUP_INSTANCE_ID', 'unconfigured'),
                                  status=directory/(args.kind+'.json'))
        alert(context, 'systemd backup job failed; inspect its journal and recovery state')
        return
    cfg = Config(args.kind, args.action)
    cfg.prepare(check_owner=args.action in ('backup', 'archive', 'check-config'))
    with cfg.lock.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError('Another operation owns the snapshot/recovery lock')
        apps = Applications(cfg)
        try:
            apps.load()
            if args.action == 'check-config':
                if cfg.required:
                    validate_db(cfg.required)
                repository(cfg)
                log(f'Configuration valid: {cfg.kind}, instance={cfg.instance}, snapshot={cfg.target}')
                return
            if apps.pending:
                apps.resume()
            if args.action == 'recover':
                return
            if args.action == 'alert':
                alert(cfg, 'systemd backup job failed; inspect its journal and recovery state')
                return
            if args.action != 'archive':
                repository(cfg)
            if args.action == 'restore':
                restore(cfg, args.extra[0] if args.extra else 'latest')
                return
            if args.action == 'restic':
                if not args.extra or args.extra[0] not in ('snapshots', 'check', 'ls', 'stats', 'cat', 'dump'):
                    raise BackupError('Helper allows read/check commands; use backup/restore entry points for mutations')
                run(['restic', *args.extra], cfg.restic_timeout)
                return
            if args.action == 'backup':
                if cfg.required:
                    validate_db(cfg.required)
                ensure_repo(cfg)
            target = snapshot(cfg)
            if args.action == 'backup':
                run(['restic', 'backup', target, '--host', cfg.host, '--tag', cfg.kind, '--tag', cfg.tag, '--exclude-caches'], cfg.restic_timeout)
                if flag('RESTIC_CHECK_AFTER', True):
                    run(['restic', 'check'], cfg.restic_timeout)
                retention(cfg)
            else:
                name = f'critical-{cfg.instance}-{dt.datetime.now().strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}.tar.zst'
                archive = cfg.root/name
                partial = cfg.root/('.'+name+'.partial')
                try:
                    run(['tar', '-C', target, '-I', 'zstd -T2 -3', '-cpf', partial, '.'], cfg.restic_timeout)
                    partial.rename(archive)
                finally:
                    partial.unlink(missing_ok=True)
                hasher = hashlib.sha256()
                with archive.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024*1024), b''):
                        hasher.update(chunk)
                digest = hasher.hexdigest()
                (cfg.root/(name+'.sha256')).write_text(digest+'  '+name+'\n')
                days = int(os.environ.get('RETENTION_DAYS', 30))
                if days < 0:
                    raise BackupError('RETENTION_DAYS must not be negative')
                if days:
                    for old in cfg.root.glob('critical-'+cfg.instance+'-*.tar.zst'):
                        checksum = Path(str(old)+'.sha256')
                        if not old.is_symlink() and checksum.is_file() and time.time()-old.stat().st_mtime > days*86400:
                            old.unlink()
                            checksum.unlink()
                log(f'Archive ready: {archive}')
            success(cfg)
            log(cfg.kind+' backup complete')
        except BaseException as exc:
            alert(cfg, str(exc))
            raise


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
