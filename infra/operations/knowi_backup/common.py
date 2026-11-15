"""Strict names, atomic metadata and bounded retention for managed backups."""
from pathlib import Path
import contextlib, hashlib, json, os, re

ROOT = Path('/var/lib/knowi-backup')
ARCHIVES = Path('/var/backups/knowi')
CONFIG = Path('/etc/knowi-backup/config.json')
IDENTIFIER = re.compile(r'knowi-core-\d{8}T\d{6}Z-[0-9a-f]{8}')

def checked_id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('invalid_backup_id')
    return value

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.new')
    with tmp.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(tmp, path)
    if os.name != 'nt':
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)

@contextlib.contextmanager
def lock():
    import fcntl
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (ROOT/'operation.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield

def records():
    result = []
    for path in sorted(ARCHIVES.glob('knowi-core-*.json')):
        item = json.loads(path.read_text())
        checked_id(item['id'])
        if path.name != item['id']+'.json' or item.get('server_restore_verified') is not True:
            raise RuntimeError('backup_manifest_invalid')
        archive = ARCHIVES/(item['id']+'.cms')
        if archive.is_symlink() or not archive.is_file() or archive.stat().st_size != item['bytes']:
            raise RuntimeError('backup_archive_missing_or_changed')
        result.append(item)
    return result

def removable(items):
    """Keep the newest four locally verified points, independently of offsite."""
    ordered = sorted(items, key=lambda x:x['id'])
    if any(x.get('server_restore_verified') is not True for x in ordered):
        raise RuntimeError('rotation_requires_locally_verified_backups')
    return ordered[:-4]

def prune():
    items=records()
    victims=removable(items)
    # Verify retained recovery points before deleting any older archive.
    victim_ids={x['id'] for x in victims}
    for item in items:
        if item['id'] not in victim_ids and sha(ARCHIVES/(item['id']+'.cms'))!=item['sha256']:
            raise RuntimeError('retained_backup_checksum_mismatch')
    removed=[]
    for item in victims:
        key=checked_id(item['id'])
        archive=ARCHIVES/(key+'.cms')
        if sha(archive) != item['sha256']: raise RuntimeError('rotation_checksum_mismatch')
        archive.unlink()
        (ARCHIVES/(key+'.json')).unlink()
        removed.append(key)
    return removed
