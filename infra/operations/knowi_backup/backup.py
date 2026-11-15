"""Create, privately restore-test, encrypt and publish one live Core snapshot."""
import datetime as dt
import json, os, re, shutil, socket, subprocess, sys, tarfile, tempfile, uuid
from pathlib import Path
from common import ROOT, ARCHIVES, CONFIG, atomic_json, lock, records, prune, sha

os.umask(0o077)
K=['/usr/local/bin/k3s','kubectl','--context','default','--request-timeout=20s','--cache-dir=/run/knowi-backup/kube-cache']

def call(args, *, data=None, output=None, timeout=120):
    result=subprocess.run(args,input=data,stdout=output or subprocess.PIPE,stderr=subprocess.PIPE,
                          timeout=timeout)
    if result.returncode: raise RuntimeError('command_failed_'+Path(args[0]).name)
    return result.stdout

def sql(ex, database, query):
    return call(ex+['psql','-XAtq','-v','ON_ERROR_STOP=1','-p','57152','-U','postgres',
                    '-d',database,'-c',query]).decode().strip()

def source(cfg):
    if socket.gethostname()!=cfg['host']: raise RuntimeError('wrong_host')
    node=json.loads(call(K+['get','node','knowi01','-o','json']))
    if node['metadata']['uid']!=cfg['node_uid']: raise RuntimeError('wrong_node')
    ns=cfg['namespace']
    dep=json.loads(call(K+['-n',ns,'get','deployment','rehearsal-postgres','-o','json']))
    if dep['metadata']['uid']!=cfg['deployment_uid']: raise RuntimeError('wrong_database_workload')
    pods=json.loads(call(K+['-n',ns,'get','pod','-l','app=rehearsal-postgres','-o','json']))['items']
    ready=[p for p in pods if p['status']['phase']=='Running' and any(s['name']=='postgres' and s['ready'] for s in p['status'].get('containerStatuses',[]))]
    if len(ready)!=1: raise RuntimeError('database_pod_not_unique_ready')
    pod=ready[0]
    rs=next(x for x in pod['metadata']['ownerReferences'] if x.get('controller'))
    owner=json.loads(call(K+['-n',ns,'get','replicaset',rs['name'],'-o','json']))
    if not any(x['uid']==cfg['deployment_uid'] for x in owner['metadata']['ownerReferences']):
        raise RuntimeError('database_pod_owner_mismatch')
    ex=K+['-n',ns,'exec','-i',pod['metadata']['name'],'-c','postgres','--']
    identity=json.loads(sql(ex,cfg['database'],"SELECT json_build_object('system_identifier',(SELECT system_identifier::text FROM pg_control_system()),'database_oid',(SELECT oid::text FROM pg_database WHERE datname=current_database()),'schema',(SELECT version_num FROM alembic_version),'version',current_setting('server_version'));"))
    if any(identity[k]!=cfg[k] for k in ('system_identifier','database_oid')) or not identity['version'].startswith('16.'):
        raise RuntimeError('database_identity_mismatch')
    # Resolve the running Core configuration without disclosing its password.
    corepods=json.loads(call(K+['-n','pymentor','get','pods','-o','json']))['items']
    core=[p for p in corepods if p['metadata']['name'].startswith('core-api-progressive-') and p['status']['phase']=='Running']
    if len(core)!=1: raise RuntimeError('core_identity_ambiguous')
    code="import os,json;from urllib.parse import urlparse;u=urlparse(os.environ['DATABASE_URL']);print(json.dumps([u.hostname,u.port,u.path.lstrip('/')]))"
    target=json.loads(call(K+['-n','pymentor','exec',core[0]['metadata']['name'],'-c','core-api','--','python','-c',code]))
    if target!=[cfg['database_host'],57152,cfg['database']]: raise RuntimeError('core_database_changed')
    return ex,identity

def backup():
    cfg=json.loads(CONFIG.read_text())
    with lock():
        prior=records()
        # Reconcile a previously published fifth point after an interrupted run.
        if len(prior)>4:
            prune()
            prior=records()
        free=shutil.disk_usage(ARCHIVES)
        estimate=max([x['bytes'] for x in prior]+[1024*1024])
        if free.free<max(free.total//10,estimate*2)+estimate: raise RuntimeError('insufficient_disk_reserve')
        ex,identity=source(cfg)
        stale=sql(ex,'postgres',"SELECT count(*) FROM pg_database WHERE datname ~ '^knowi_backup_verify_[0-9a-f]{16}$';")
        if stale!='0': raise RuntimeError('stale_restore_verification_requires_reconcile')
        stamp=dt.datetime.now(dt.timezone.utc)
        key='knowi-core-'+stamp.strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        scratch='knowi_backup_verify_'+uuid.uuid4().hex[:16]
        runtime=Path('/run/knowi-backup')
        runtime.mkdir(mode=0o700,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='work-',dir=runtime) as work:
            directory=Path(work)
            dump=directory/'database.dump'
            with dump.open('xb') as stream:
                call(ex+['pg_dump','-p','57152','-U','postgres','-d',cfg['database'],'-Fc','--compress=6'],output=stream,timeout=600)
                stream.flush(); os.fsync(stream.fileno())
            if dump.stat().st_size<6 or dump.read_bytes()[:5]!=b'PGDMP': raise RuntimeError('invalid_dump')
            call(ex+['pg_restore','--list'],data=dump.read_bytes())
            created=False
            try:
                call(ex+['createdb','-p','57152','-U','postgres','--template=template0',scratch])
                created=True
                sql(ex,'postgres',f'REVOKE ALL ON DATABASE {scratch} FROM PUBLIC; REVOKE CONNECT ON DATABASE {scratch} FROM knowi_app,knowi_migrator;')
                call(ex+['pg_restore','-p','57152','-U','postgres','--exit-on-error','--no-owner','--no-privileges','-d',scratch],data=dump.read_bytes(),timeout=600)
                restored=json.loads(sql(ex,scratch,"SELECT json_build_object('schema',(SELECT version_num FROM alembic_version),'users',(SELECT count(*) FROM users),'deletions',(SELECT count(*) FROM account_deletion_events),'invalid_constraints',(SELECT count(*) FROM pg_constraint WHERE NOT convalidated));"))
                if restored['schema']!=identity['schema'] or restored['invalid_constraints']!=0:
                    raise RuntimeError('restored_schema_verification_failed')
            finally:
                if created: call(ex+['dropdb','-p','57152','-U','postgres',scratch])
            ledger=sql(ex,cfg['database'],"SELECT COALESCE(json_agg(json_build_object('lifecycle_id',lifecycle_id,'user_id',user_id,'deleted_at',deleted_at) ORDER BY lifecycle_id),'[]'::json) FROM account_deletion_events;")
            # Independent, cumulative export; never automatically purge markers.
            old=json.loads((ROOT/'deletion-ledger.json').read_text()) if (ROOT/'deletion-ledger.json').exists() else []
            union={x['lifecycle_id']:x for x in old}
            for row in json.loads(ledger):
                if row['lifecycle_id'] in union and union[row['lifecycle_id']]['user_id']!=row['user_id']:
                    raise RuntimeError('deletion_ledger_conflict')
                union[row['lifecycle_id']]=row
            ledger=list(sorted(union.values(),key=lambda x:x['lifecycle_id']))
            atomic_json(ROOT/'deletion-ledger.json',ledger)
            (directory/'deletion-events.json').write_text(json.dumps(ledger))
            info={'id':key,'database':cfg['database'],'created_at':stamp.isoformat(),'source':identity,
                  'restored':restored,'dump_sha256':sha(dump),'journal_complete_for_disaster_restore':False}
            (directory/'metadata.json').write_text(json.dumps(info))
            tar=directory/'payload.tar.gz'
            with tarfile.open(tar,'w:gz') as archive:
                for filename in ('database.dump','metadata.json','deletion-events.json'):
                    archive.add(directory/filename,arcname=filename)
            cipher=ARCHIVES/('.'+key+'.pending')
            call(['/usr/bin/openssl','cms','-encrypt','-binary','-aes-256-gcm','-outform','DER',
                  '-recip','/etc/knowi-backup/recipient.pem','-keyopt','rsa_padding_mode:oaep',
                  '-in',str(tar),'-out',str(cipher)])
            with cipher.open('rb') as stream: os.fsync(stream.fileno())
            final=ARCHIVES/(key+'.cms')
            if final.exists(): raise RuntimeError('backup_id_collision')
            os.replace(cipher,final)
            item={'id':key,'created_at':stamp.isoformat(),'bytes':final.stat().st_size,'sha256':sha(final),
                  'payload_sha256':sha(tar),'dump_sha256':sha(dump),'server_restore_verified':True,
                  'offsite_verified':False,'source':identity,'format':'CMS-AES256-GCM-RSA-OAEP-tar.gz'}
            atomic_json(ARCHIVES/(key+'.json'),item)
            removed=prune()
            atomic_json(ROOT/'status.json',{'status':'created_waiting_for_offsite','latest':key,'points':len(records())})
            print(json.dumps({'status':'created_waiting_for_offsite','id':key,'bytes':item['bytes'],'restore':'verified','removed':removed}))

if __name__=='__main__':
    try: backup()
    except Exception as exc:
        try: atomic_json(ROOT/'last-error.json',{'status':'failed','error_type':type(exc).__name__,'reason':str(exc) if type(exc) is RuntimeError else 'details_redacted'})
        except Exception: pass
        print('BACKUP FAILED: '+(str(exc) if type(exc) is RuntimeError else type(exc).__name__),file=sys.stderr)
        raise SystemExit(1)
