"""Recreate the accepted second course in a disposable directory, without a DB."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'services/core-api'))
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
os.environ['PYTHON_DOTENV_DISABLED']='1'
from pymentor_core.adapters.course_local.runtime import CourseRuntime
from pymentor_core.adapters.course_local.personal_settings import read_personal_settings

out=ROOT/'services/core-api/.local/fixtures'/('creator-'+uuid4().hex)
out.mkdir(parents=True,exist_ok=False)
root=out/'courses';root.mkdir()
source=ROOT/'courses/backend'
# Catalog helpers are part of the trusted backend digest, outside each product.
shutil.copytree(ROOT/'courses/shared/backend',root/'shared/backend',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
def hashes(directory):
    return {str(p.relative_to(directory)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
for p in source.rglob('*'):
    if p.is_symlink() or os.path.isjunction(p):raise RuntimeError('Authoring links are forbidden')
    if p.is_file() and (p.name=='.env' or p.suffix.lower() in {'.db','.sqlite','.sqlite3'}):raise RuntimeError('Runtime data cannot be copied as a course')
for course in ('backend','backend_copy'):
    dest=root/course
    shutil.copytree(source,dest,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    if course=='backend_copy':
        path=dest/'course.yaml';manifest=json.loads(path.read_text(encoding='utf-8'))
        manifest.update(course_id=course,slug=course,title='Python Backend Copy')
        path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        path=dest/'personal.settings.yaml';value=path.read_text(encoding='utf-8')
        assert 'course_id: backend\n' in value
        path.write_text(value.replace('course_id: backend\n','course_id: backend_copy\n'),encoding='utf-8')
        path=dest/'frontend/landing-personal/index.ts';value=path.read_text(encoding='utf-8')
        assert 'courseId: "backend"' in value
        path.write_text(value.replace('courseId: "backend"','courseId: "backend_copy"'),encoding='utf-8')
registry={'schema_version':1,'courses':[{'course_id':c,'enabled':True,'manifest':f'{c}/course.yaml'} for c in ('backend','backend_copy')]}
(root/'registry.json').write_text(json.dumps(registry,indent=2),encoding='utf-8')
runtime=CourseRuntime(root)
a=runtime.snapshot('backend');b=runtime.snapshot('backend_copy')
assert a.catalog.TRACKS==b.catalog.TRACKS
assert hashes(source/'backend')==hashes(root/'backend_copy/backend')
assert hashes(source/'frontend/course')==hashes(root/'backend_copy/frontend/course')
for course in ('backend','backend_copy'):
    settings=read_personal_settings(runtime.registry,course)
    assert settings.course_id==course and not settings.telegram_enabled and settings.chat_id is None
original=CourseRuntime(ROOT/'courses').snapshot('backend')
assert a.catalog.get_content_revision()==original.catalog.get_content_revision()
assert a.catalog.TRACKS==original.catalog.TRACKS
assert a.catalog.LESSONS==original.catalog.LESSONS
assert 'pymentor_core.app.database' not in sys.modules and 'pymentor_core.app.factory' not in sys.modules
report={'result':'PASS','fixture':str(out.relative_to(ROOT)),
        'tracks_per_course':len(a.catalog.TRACKS),'lessons_per_course':len(a.catalog.LESSONS),
        'tasks_per_course':sum(len(l['tasks']) for l in a.catalog.LESSONS),
        'backend_files':len(hashes(source/'backend')),'frontend_course_files':len(hashes(source/'frontend/course')),
        'backend_and_frontend_bytes_equal':True,'personal_settings_inactive':True,'database_imported':False,
        'limitation':'Fixture loader and source-byte proof; actual frontend build/browser accepted separately, no third permanent course'}
target=out/'report';target.mkdir(parents=True,exist_ok=True)
(target/'creator-rehearsal.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
