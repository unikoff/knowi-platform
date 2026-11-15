"""Restricted, pinned SSH pull. Keys and verified encrypted copies stay local."""
from pathlib import Path
import contextlib, datetime as dt, hashlib, json, msvcrt, os, shutil, subprocess, sys, tarfile, tempfile
import paramiko
from common import checked_id, sha, removable

ROOT=Path(__file__).resolve().parents[1]

def store(path,data):
    tmp=path.with_suffix('.new')
    with tmp.open('w',encoding='utf-8') as stream:
        json.dump(data,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
    os.replace(tmp,path)

@contextlib.contextmanager
def lock():
    with (ROOT/'state/pull.lock').open('a+b') as stream:
        stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
        msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        try: yield
        finally:
            stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)

def command(client,text,destination=None,max_bytes=2*1024*1024):
    _,out,err=client.exec_command(text,timeout=180)
    count=0; chunks=[]
    while block:=out.read(1024*1024):
        count+=len(block)
        if count>max_bytes: raise RuntimeError('remote_response_too_large')
        if destination is None:
            chunks.append(block)
        else: destination.write(block)
    failure=err.read(8192)
    if out.channel.recv_exit_status()!=0: raise RuntimeError('restricted_endpoint_refused')
    return b''.join(chunks) if destination is None else count

def verify(path,item,cfg):
    if path.stat().st_size!=item['bytes'] or sha(path)!=item['sha256']:
        raise RuntimeError('download_checksum_mismatch')
    with tempfile.TemporaryDirectory(prefix='verify-',dir=ROOT/'verify') as name:
        directory=Path(name).resolve()
        assert directory.parent==(ROOT/'verify').resolve()
        payload=directory/'payload.tar.gz'
        result=subprocess.run([cfg['openssl'],'cms','-decrypt','-binary','-inform','DER',
            '-recip',str(ROOT/'keys/recipient.pem'),'-inkey',str(ROOT/'keys/decrypt.pem'),
            '-in',str(path),'-out',str(payload)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120)
        if result.returncode or sha(payload)!=item['payload_sha256']: raise RuntimeError('decryption_or_payload_check_failed')
        with tarfile.open(payload,'r:gz') as archive:
            members=archive.getmembers()
            if len(members)!=3 or {x.name for x in members}!={'database.dump','metadata.json','deletion-events.json'} or any(not x.isfile() or x.size>512*1024*1024 for x in members):
                raise RuntimeError('backup_bundle_shape_invalid')
            metadata=json.load(archive.extractfile('metadata.json'))
            if metadata['id']!=item['id'] or metadata['dump_sha256']!=item['dump_sha256']:
                raise RuntimeError('backup_metadata_mismatch')
            with archive.extractfile('database.dump') as stream:
                digest=hashlib.file_digest(stream,'sha256').hexdigest()
            if digest!=item['dump_sha256']: raise RuntimeError('dump_checksum_mismatch')

def pull():
    cfg=json.loads((ROOT/'client.json').read_text())
    with lock():
        client=paramiko.SSHClient()
        client.load_host_keys(str(ROOT/'keys/known_hosts'))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(cfg['host'],port=cfg['port'],username=cfg['user'],
            key_filename=str(ROOT/'keys/pull_ed25519'),look_for_keys=False,allow_agent=False,
            timeout=15,banner_timeout=15,auth_timeout=15)
        downloaded=[]
        try:
            items=json.loads(command(client,'list'))
            if not isinstance(items,list) or len(items)>5: raise RuntimeError('remote_inventory_invalid')
            for item in sorted(items,key=lambda x:x['id']):
                key=checked_id(item['id'])
                if type(item['bytes']) is not int or not 0<item['bytes']<=1024**3:
                    raise RuntimeError('archive_size_invalid')
                final=ROOT/'archives'/(key+'.cms')
                if not final.exists():
                    free=shutil.disk_usage(ROOT)
                    if free.free<2*1024**3+item['bytes']*4: raise RuntimeError('local_disk_reserve_low')
                    part=ROOT/'archives'/(key+'.part')
                    with part.open('wb') as stream:
                        count=command(client,'read '+key,stream,max_bytes=item['bytes'])
                        stream.flush();os.fsync(stream.fileno())
                    if count!=item['bytes']: raise RuntimeError('download_size_mismatch')
                    verify(part,item,cfg)
                    os.replace(part,final)
                    downloaded.append(key)
                else: verify(final,item,cfg)
                ack=json.loads(command(client,'ack '+key+' '+item['sha256']))
                if ack.get('acknowledged')!=key: raise RuntimeError('offsite_ack_mismatch')
                item['offsite_verified']=True
                store(ROOT/'archives'/(key+'.json'),item)
            local=[]
            for path in (ROOT/'archives').glob('knowi-core-*.json'):
                item=json.loads(path.read_text());checked_id(item['id'])
                if path.name!=item['id']+'.json': raise RuntimeError('local_manifest_name_invalid')
                local.append(item)
            for item in removable(local):
                archive=ROOT/'archives'/(checked_id(item['id'])+'.cms')
                if sha(archive)!=item['sha256']: raise RuntimeError('local_rotation_checksum_mismatch')
                archive.unlink();(ROOT/'archives'/(item['id']+'.json')).unlink()
            return {'status':'ok','checked':len(items),'downloaded':len(downloaded),
                    'latest':items[-1]['id'] if items else None,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
        finally: client.close()

if __name__=='__main__':
    try:
        result=pull();store(ROOT/'state/last-result.json',result)
        if sys.stdout is not None: print(json.dumps(result))
    except Exception as exc:
        result={'status':'failed','error_type':type(exc).__name__,'reason':str(exc) if type(exc) is RuntimeError else 'details_redacted',
                'at':dt.datetime.now(dt.timezone.utc).isoformat()}
        store(ROOT/'state/last-result.json',result)
        if sys.stdout is not None: print(json.dumps(result))
        raise SystemExit(1)
