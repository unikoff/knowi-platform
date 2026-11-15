"""Repin one trusted course after authoring changes, preserving manifest text."""
from __future__ import annotations
import argparse, os, re, sys
sys.dont_write_bytecode = True
from pathlib import Path
from uuid import uuid4
from create_course import ROOT, registry_lock


def replace_checked(path, before, after):
    if path.read_bytes() != before:
        raise RuntimeError('Manifest changed concurrently; nothing was overwritten')
    temporary=path.with_name('.pin-'+uuid4().hex+'.tmp')
    with temporary.open('xb') as output:
        output.write(after);output.flush();os.fsync(output.fileno())
    if path.read_bytes()!=before:
        temporary.unlink()
        raise RuntimeError('Manifest changed concurrently; nothing was overwritten')
    os.replace(temporary,path)


def pin(root: Path, course_id: str):
    root=root.resolve(strict=True)
    os.environ.update(PYTHON_DOTENV_DISABLED='1',PYTHONDONTWRITEBYTECODE='1')
    sys.path.insert(0,str(root/'services/core-api'))
    from pymentor_core.adapters.course_local.packages import load_registry,load_course
    from pymentor_core.adapters.course_local.runtime import CourseRuntime,content_version
    with registry_lock(root):
        registry=load_registry(root/'courses')
        registry.require_enabled(course_id)
        manifest=load_course(registry,course_id)
        folder=(root/'courses'/course_id).resolve(strict=True)
        path=folder/'course.yaml'
        before=path.read_bytes()
        digest=content_version(folder,manifest)
        pattern=rb'(?m)(^[ \t]*["\x27]?content_version["\x27]?[ \t]*:[ \t]*["\x27]?)([0-9a-f]{64})'
        matches=list(re.finditer(pattern,before))
        if len(matches)!=1 or matches[0][2].decode()!=manifest.content_version:
            raise ValueError('Put content_version on its own mapping line; manifest was not rewritten')
        after=re.sub(pattern,lambda m:m[1]+digest.encode(),before,count=1)
        if after!=before:replace_checked(path,before,after)
        try:
            snapshot=CourseRuntime(root/'courses').snapshot(course_id)
        except BaseException:
            if after!=before and path.read_bytes()==after:replace_checked(path,after,before)
            raise
        print(f'{course_id}: validated {len(snapshot.catalog.LESSONS)} lessons; content_version={digest}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('course_id');parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args()
    try:pin(args.root,args.course_id)
    except (ValueError,RuntimeError,OSError,LookupError) as error:
        print(str(error),file=sys.stderr);return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
