"""Age rotation through CRI ReopenContainerLog; never copytruncate live logs."""
from pathlib import Path
import datetime as dt
import gzip, hashlib, json, os, re, shutil, stat, subprocess, sys, time, uuid
from common import atomic_json

PODS=Path('/var/log/pods')
ARCHIVE=Path('/var/log/knowi-archives')
STATE=Path('/var/lib/knowi-logs')
LIMIT=100*1024*1024
DAY=24*3600

def run(args):
    result=subprocess.run(args,capture_output=True,timeout=30)
    if result.returncode: raise RuntimeError('log_inventory_failed')
    return json.loads(result.stdout)

def group(path):
    rel=path.relative_to(PODS)
    ns=rel.parts[0].split('_',1)[0]
    name=rel.parts[1]
    if not re.fullmatch('[a-z0-9.-]+',ns) or not re.fullmatch('[a-z0-9.-]+',name):
        raise RuntimeError('log_path_invalid')
    return ns+'__'+name

def age(path):
    try:
        opener=gzip.open if path.suffix=='.gz' else open
        with opener(path,'rb') as stream:
            first=stream.readline(256).split(b' ',1)[0].decode()
        stamp=dt.datetime.fromisoformat(first.replace('Z','+00:00')).timestamp()
        return time.time()-stamp
    except Exception: return time.time()-path.stat().st_mtime

def reopen(container_id):
    import grpc
    if not re.fullmatch('[0-9a-f]{64}',container_id): raise RuntimeError('invalid_container_id')
    # runtime.v1.ReopenContainerLogRequest: field 1, string container_id (64 bytes).
    payload=b'\x0a\x40'+container_id.encode('ascii')
    with grpc.insecure_channel('unix:///run/k3s/containerd/containerd.sock') as channel:
        rpc=channel.unary_unary('/runtime.v1.RuntimeService/ReopenContainerLog')
        rpc(payload,timeout=10)

def staged(record):
    return (PODS if record.get('version')==2 else ARCHIVE)/record['stage']


def sync_dir(path):
    if os.name=='nt': return
    descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def finish(record):
    pending=staged(record)
    original=PODS/record['original']
    if record['running'] and pending.stat().st_ino in writable_inodes():
        # A lost reply may already have reopened the writer. Reconcile paths and
        # actual writable descriptors before issuing the same safe CRI request.
        reopen(record['container_id'])
        if not original.is_file() or original.stat().st_ino==pending.stat().st_ino:
            raise RuntimeError('log_reopen_not_verified')
    if pending.stat().st_ino in writable_inodes(): raise RuntimeError('log_writer_still_open')
    final=(ARCHIVE/group(original)/(record['token']+'.log.gz')
           if record.get('version')==2 else pending.with_suffix('.log.gz'))
    if not final.exists():
        temp=final.with_suffix('.tmp')
        if temp.is_symlink(): raise RuntimeError('log_staging_symlink')
        # The checkpoint owns this unique temporary name. Rebuild an interrupted
        # compression from the still intact, closed original.
        with pending.open('rb') as source, temp.open('wb') as target:
            with gzip.GzipFile(fileobj=target,mode='wb',mtime=0) as compressor:
                shutil.copyfileobj(source,compressor)
            target.flush();os.fsync(target.fileno())
        os.replace(temp,final)
        sync_dir(final.parent)
    with gzip.open(final,'rb') as restored, pending.open('rb') as source:
        if hashlib.file_digest(restored,'sha256').digest()!=hashlib.file_digest(source,'sha256').digest():
            raise RuntimeError('log_archive_verification_failed')
    pending.unlink()
    sync_dir(pending.parent)

def rotate(path,container_id=None):
    name=group(path)
    directory=ARCHIVE/name;directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    token=uuid.uuid4().hex
    # Stay within the original directory: separate systemd ReadWritePaths are
    # separate mounts. This hidden name is outside kubelet's 0.log.* glob.
    stage=path.with_name('.knowi-'+token+'.pending')
    record={'version':2,'token':token,'original':str(path.relative_to(PODS)),
            'stage':str(stage.relative_to(PODS)),
            'running':container_id is not None,'container_id':container_id}
    checkpoint=STATE/(token+'.json')
    atomic_json(checkpoint,record)
    try: os.rename(path,stage)
    except FileNotFoundError:
        checkpoint.unlink();return
    sync_dir(path.parent)
    finish(record)
    checkpoint.unlink()

def writable_inodes():
    result=set()
    for process in Path('/proc').iterdir():
        if not process.name.isdigit(): continue
        try:
            for fd in (process/'fd').iterdir():
                try:
                    dest=os.readlink(fd)
                    if not dest.startswith(str(PODS)+'/') and not dest.startswith(str(ARCHIVE)+'/'): continue
                    flags=next(x for x in (process/'fdinfo'/fd.name).read_text().splitlines() if x.startswith('flags:'))
                    if int(flags.split()[1],8)&os.O_ACCMODE: result.add(fd.stat().st_ino)
                except (OSError,StopIteration): pass
        except OSError: pass
    return result

def maintain(force=None):
    import fcntl
    os.umask(0o077)
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True);ARCHIVE.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (STATE/'operation.lock').open('a') as gate:
        fcntl.flock(gate,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for checkpoint in STATE.glob('*.json'):
            if checkpoint.name=='status.json': continue
            record=json.loads(checkpoint.read_text())
            pending=staged(record)
            if pending.is_file(): finish(record)
            checkpoint.unlink()
        inventory=run(['/usr/local/bin/k3s','crictl','ps','-o','json'])['containers']
        active={}
        for item in inventory:
            labels=item['labels'];meta=item['metadata']
            path=PODS/(labels['io.kubernetes.pod.namespace']+'_'+labels['io.kubernetes.pod.name']+'_'+labels['io.kubernetes.pod.uid'])/meta['name']/(str(meta['attempt'])+'.log')
            active[path]=item['id']
        changed=0
        for path,cid in active.items():
            if not path.is_file() or path.is_symlink() or path.stat().st_size==0: continue
            if force==cid or age(path)>DAY:
                detail=run(['/usr/local/bin/k3s','crictl','inspect',cid])['status']
                if Path(detail['logPath'])!=path: raise RuntimeError('cri_log_path_mismatch')
                rotate(path,cid);changed+=1
        opened=writable_inodes()
        allfiles=[]
        for path in PODS.glob('*/*/*.log*'):
            if path.is_symlink() or not path.is_file() or path.suffix=='.tmp': continue
            if path not in active and path.stat().st_ino not in opened:
                if age(path)>14*DAY:
                    path.unlink();continue
                if path.suffix!='.gz':
                    rotate(path);changed+=1;continue
            allfiles.append((group(path),path))
        for directory in ARCHIVE.iterdir():
            if not directory.is_dir() or directory.is_symlink(): continue
            for path in directory.glob('*.log.gz'):
                if age(path)>14*DAY: path.unlink()
                else: allfiles.append((directory.name,path))
        grouped={}
        for key,path in allfiles: grouped.setdefault(key,[]).append(path)
        totals={}
        for key,paths in grouped.items():
            total=sum(x.stat().st_size for x in paths if x.exists())
            for path in sorted(paths,key=lambda x:x.stat().st_mtime):
                if total<=LIMIT: break
                if path in active or path.stat().st_ino in opened: continue
                size=path.stat().st_size;path.unlink();total-=size
            if total>LIMIT: raise RuntimeError('active_log_budget_exceeded')
            totals[key]=total
        atomic_json(STATE/'status.json',{'at':dt.datetime.now(dt.timezone.utc).isoformat(),'rotated':changed,'bytes_by_service':totals})
        print(json.dumps({'rotated':changed,'services':len(totals),'max_service_bytes':max(totals.values(),default=0)}))

if __name__=='__main__':
    try: maintain(sys.argv[1] if len(sys.argv)>1 else None)
    except Exception as exc:
        print('LOG MAINTENANCE FAILED: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1)
