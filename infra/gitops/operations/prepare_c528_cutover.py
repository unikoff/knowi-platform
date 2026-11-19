"""Prepare c504 -> c528 artifacts offline; never connect to a cluster."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location('c504_cutover_helpers', Path(__file__).with_name('prepare_c504_cutover.py'))
_old = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_old)
DIGEST_KEYS = ('core_digest', 'frontend_digest', 'runner_digest', 'migration_digest', 'analytics_digest')
HOST = _old.HOST
NEW_PATH = 'infra/gitops/preview'


def prepare(release_file, code_revision, gitops_revision, app_uid, old_path, old_revision, owner, app_role, output):
    if any(not re.fullmatch(r'[0-9a-f]{40}', x) for x in (code_revision, gitops_revision)):
        raise ValueError('Code and GitOps revisions must be immutable commit SHAs')
    try:
        if str(uuid.UUID(app_uid)) != app_uid:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ValueError('Application UID must be a canonical UUID') from None
    if not old_path or not old_revision or any(x.strip() != x for x in (old_path, old_revision)):
        raise ValueError('Current Application path and revision are required')
    login = 'knowi_c528_' + code_revision[:12]
    if any(not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', x) for x in (owner, app_role)) or len({login, owner, app_role}) != 3:
        raise ValueError('Migration login, owner and application role must be distinct valid role names')
    release = json.loads(Path(release_file).read_text(encoding='utf-8-sig'))
    if not isinstance(release, dict) or release.get('source_sha') != code_revision or any(
        not isinstance(release.get(k), str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', release[k]) for k in DIGEST_KEYS
    ):
        raise ValueError('Release must match source SHA and contain all five immutable image digests')
    try:
        published = subprocess.run(['git', 'show', gitops_revision + ':' + NEW_PATH + '/release.json'], cwd=ROOT,
                                   capture_output=True, check=True, timeout=20)
        pinned_release = json.loads(published.stdout.decode('utf-8-sig'))
    except (subprocess.SubprocessError, UnicodeError, ValueError) as exc:
        raise ValueError('Cannot read release.json from the pinned GitOps commit') from exc
    if release != pinned_release:
        raise ValueError('Release file differs from release.json in the pinned GitOps commit')
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a new output directory; existing artifacts are never overwritten')
    identity = {'op': 'test', 'path': '/metadata/uid', 'value': app_uid}
    old_source = [{'op': 'test', 'path': '/spec/source/path', 'value': old_path},
                  {'op': 'test', 'path': '/spec/source/targetRevision', 'value': old_revision}]
    pause = [identity, *old_source, {'op': 'test', 'path': '/spec/syncPolicy/automated/enabled', 'value': True},
             {'op': 'replace', 'path': '/spec/syncPolicy/automated/enabled', 'value': False}]
    switch = [identity, *old_source, {'op': 'test', 'path': '/spec/syncPolicy/automated/enabled', 'value': False},
              {'op': 'replace', 'path': '/spec/source/targetRevision', 'value': gitops_revision},
              {'op': 'replace', 'path': '/spec/source/path', 'value': NEW_PATH}]
    policy = _old.migration_network_policy(code_revision)
    policy['metadata']['name'] = 'c528-migrator-' + code_revision
    env = [
        {'name': 'KNOWI_MIGRATION_URL', 'valueFrom': {'secretKeyRef': {'name': 'knowi-core-migrations', 'key': 'KNOWI_MIGRATION_URL'}}},
        *[{'name': name, 'value': value} for name, value in (
            ('KNOWI_MIGRATION_HOST', HOST), ('KNOWI_MIGRATION_DATABASE', 'knowi_core'),
            ('KNOWI_MIGRATION_USER', login), ('KNOWI_MIGRATION_OWNER', owner), ('KNOWI_MIGRATION_APP_ROLE', app_role))]]
    job = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {
        'name': 'knowi-c528-' + code_revision[:12], 'namespace': 'pymentor',
        'annotations': {'knowi.io/code-revision': code_revision}}, 'spec': {
        'backoffLimit': 0, 'activeDeadlineSeconds': 300, 'template': {
            'metadata': {'labels': {'app.kubernetes.io/component': 'migrations', 'knowi.io/source-sha': code_revision}},
            'spec': {'restartPolicy': 'Never', 'automountServiceAccountToken': False,
                'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001},
                'containers': [{'name': 'migrate', 'image': 'ghcr.io/unikoff/knowi-platform-core-migrations@' + release['migration_digest'],
                    'command': ['python', '-m', 'alembic', '-c', '/app/services/core-api/alembic-runtime.ini', 'upgrade', 'c528_course_leaders'],
                    'env': env, 'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '1', 'memory': '256Mi'}},
                    'securityContext': {'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}}}]}}}}
    artifacts = {'01-pause-application.patch.json': pause, '02-migrator-networkpolicy.json': policy,
                 '03-migration-job.json': job, '04-switch-pinned-path.patch.json': switch}
    output.mkdir(parents=True)
    for name, document in artifacts.items():
        with (output / name).open('x', encoding='utf-8') as stream:
            json.dump(document, stream, indent=2)
            stream.write('\n')
    return artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('release-file', 'code-revision', 'gitops-revision', 'app-uid', 'old-path', 'old-revision', 'owner', 'app-role', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    try:
        prepare(args.release_file, args.code_revision, args.gitops_revision, args.app_uid, args.old_path,
                args.old_revision, args.owner, args.app_role, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + '\n')
    print('Prepared only: no cluster changes; follow c528-cutover.md.')


if __name__ == '__main__':
    main()
