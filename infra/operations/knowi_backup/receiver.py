#!/usr/bin/python3
"""Forced-command SSH endpoint: list/read/ack only, no arbitrary shell or paths."""
import json, os, re, shlex, sys
from common import ARCHIVES, ROOT, checked_id, lock, records, sha, atomic_json

def main():
    args=shlex.split(os.environ.get('SSH_ORIGINAL_COMMAND',''))
    with lock():
        if args==['list']:
            print(json.dumps(records()))
        elif len(args)==2 and args[0]=='read':
            key=checked_id(args[1])
            item=next(x for x in records() if x['id']==key)
            path=ARCHIVES/(key+'.cms')
            if sha(path)!=item['sha256']: raise RuntimeError('archive_checksum_changed')
            with path.open('rb') as stream:
                while block:=stream.read(1024*1024): sys.stdout.buffer.write(block)
        elif len(args)==3 and args[0]=='ack':
            key=checked_id(args[1])
            if not re.fullmatch('[0-9a-f]{64}',args[2]): raise ValueError('invalid_digest')
            item=next(x for x in records() if x['id']==key)
            if item['sha256']!=args[2] or sha(ARCHIVES/(key+'.cms'))!=args[2]:
                raise RuntimeError('ack_checksum_mismatch')
            item['offsite_verified']=True
            atomic_json(ARCHIVES/(key+'.json'),item)
            current=records()
            latest=current[-1]
            atomic_json(ROOT/'status.json',{'status':'verified_offsite' if latest['offsite_verified'] else 'created_waiting_for_offsite','latest':latest['id'],'points':len(current)})
            print(json.dumps({'acknowledged':key,'removed':[]}))
        else:
            raise ValueError('command_not_allowed')

if __name__=='__main__':
    try: main()
    except Exception:
        print('backup_endpoint_refused',file=sys.stderr)
        raise SystemExit(1)
