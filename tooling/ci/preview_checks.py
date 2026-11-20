"""Required PostgreSQL/Redis release checks in isolated processes."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
PG = 'postgresql+psycopg://pymentor_test:pymentor_test@127.0.0.1:57382/pymentor_test'
REDIS = 'redis://127.0.0.1:57381/0'


def run(command, env):
    subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=180)



def updater_tests():
    """Exercise release updates and rejection paths using disposable JSON fixtures."""
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location('preview_updater', ROOT / 'tooling/ci/update_preview_release.py')
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    sha = 'a' * 40
    digests = tuple('sha256:' + char * 64 for char in '123')
    with tempfile.TemporaryDirectory(prefix='preview-updater-test-') as temp:
        directory = Path(temp)
        def write(name, value):
            (directory / name).write_text(json.dumps(value), encoding='utf-8')
        def read(name):
            return json.loads((directory / name).read_text(encoding='utf-8'))
        def workload(kind):
            return {'kind': kind, 'spec': {'template': {'spec': {'containers': [
                {'name': 'app', 'env': [{'name': 'PYMENTOR_RELEASE_REVISION', 'value': 'old'}]}
            ]}}}}
        base = {'kind': 'Kustomization', 'resources': ['applications.json', 'runner.json', 'notifications.json'], 'images': []}
        write('kustomization.yaml', base)
        write('applications.json', {'kind': 'List', 'items': [workload('Rollout')]})
        write('runner.json', workload('Deployment'))
        def rejected(*args):
            try:
                updater.update(directory, *args)
            except ValueError:
                return
            raise AssertionError('invalid release accepted')
        rejected(sha, 'sha256:bad', digests[1], digests[2])
        rejected('../bad', *digests)
        assert read('kustomization.yaml') == base and not (directory / 'release.json').exists()
        invalid = dict(base, resources=['applications.json', '../other.json'])
        write('kustomization.yaml', invalid)
        rejected(sha, *digests)
        assert read('kustomization.yaml') == invalid and not (directory / 'release.json').exists()
        write('kustomization.yaml', dict(base, images=[{'name': 'unapproved/image'}]))
        rejected(sha, *digests)
        write('kustomization.yaml', base)
        updater.update(directory, sha, *digests)
        before = read('release.json')
        assert before == dict(source_sha=sha, core_digest=digests[0], frontend_digest=digests[1], runner_digest=digests[2])
        try:
            updater.update(directory, sha, *digests, migration_digest='invalid')
        except ValueError:
            pass
        else:
            raise AssertionError('invalid migration digest accepted')
        assert read('release.json') == before
        for invalid_analytics in ('', 'sha256:bad', 'sha256:' + 'A' * 64):
            snapshot = {name: (directory / name).read_bytes() for name in ('kustomization.yaml', 'applications.json', 'runner.json', 'release.json')}
            try:
                updater.update(directory, sha, *digests, analytics_digest=invalid_analytics)
            except ValueError:
                pass
            else:
                raise AssertionError('invalid analytics digest accepted')
            assert all((directory / name).read_bytes() == data for name, data in snapshot.items())
        analytics_digest = 'sha256:' + '5' * 64
        updater.update(directory, sha, *digests, analytics_digest=analytics_digest)
        assert read('release.json')['source_sha'] == sha
        assert read('release.json')['analytics_digest'] == analytics_digest
        updater.update(directory, sha, *digests, migration_digest='sha256:' + '4' * 64)
        assert read('release.json')['migration_digest'] == 'sha256:' + '4' * 64
        mappings = read('kustomization.yaml')['images']
        assert [(entry['name'], entry['newName'], entry['digest']) for entry in mappings] == [
            (name, target, digest) for (name, target), digest in zip(updater.IMAGES.items(), digests)]
        for filename in ('applications.json', 'runner.json'):
            document = read(filename)
            workload_item = document['items'][0] if document['kind'] == 'List' else document
            template = workload_item['spec']['template']
            assert template['metadata']['annotations']['pymentor.io/source-revision'] == sha
            assert template['spec']['containers'][0]['env'][0]['value'] == sha
        assert read('release.json') == dict(source_sha=sha, core_digest=digests[0], frontend_digest=digests[1], runner_digest=digests[2], migration_digest='sha256:' + '4' * 64)
        updater.update(directory, sha, *digests)
        assert len(read('runner.json')['spec']['template']['spec']['containers'][0]['env']) == 1
        # A Web-only release must not rewrite the Core and runner Pod templates.
        core = dict(workload('Rollout'), metadata={'name': 'core-api-progressive'})
        web = dict(workload('Rollout'), metadata={'name': 'frontend-progressive'})
        write('applications.json', {'kind': 'List', 'items': [core, web]})
        write('runner.json', dict(workload('Deployment'), metadata={'name': 'preview-pool-broker'}))
        image_sources = dict.fromkeys(('core', 'frontend', 'runner-pool'), sha)
        updater.update(directory, sha, *digests, image_sources=image_sources)
        before_core, before_web = read('applications.json')['items']
        before_runner = (directory / 'runner.json').read_bytes()
        new_sha = 'b' * 40
        image_sources['frontend'] = new_sha
        updater.update(directory, new_sha, digests[0], 'sha256:' + 'c' * 64, digests[2], image_sources=image_sources)
        after_core, after_web = read('applications.json')['items']
        assert before_core == after_core and before_web != after_web
        assert (directory / 'runner.json').read_bytes() == before_runner
        assert read('release.json')['image_sources'] == image_sources
    print('Preview updater fixture tests passed', flush=True)

def require_test_results(path):
    from xml.etree import ElementTree
    suites = list(ElementTree.parse(path).getroot().iter('testsuite'))
    counts = {key: sum(int(item.get(key, '0')) for item in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')}
    if counts['tests'] <= 0 or any(counts[key] for key in ('failures', 'errors', 'skipped')):
        raise RuntimeError('Critical test results missing, failed or skipped')


def pytest_checked(files, env, report, prefix='services/core-api/tests/'):
    run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--tb=short', '--maxfail=1',
         '--junitxml=' + str(report), *[prefix + name for name in files]], env)
    require_test_results(report)


def http_partition(files, group):
    """Split the ordered suite exactly once across two stable shards."""
    if group == 'http-1':
        return files[::2]
    if group == 'http-2':
        return files[1::2]
    raise ValueError(f'Unknown HTTP group: {group}')


@contextmanager
def timed(label):
    start = time.monotonic()
    status = 'passed'
    try:
        yield
    except BaseException:
        status = 'failed'
        raise
    finally:
        line = f'{label}: {time.monotonic() - start:.2f}s ({status})'
        print(line, flush=True)
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            try:
                with open(summary, 'a', encoding='utf-8') as output:
                    output.write(f'- {line}\n')
            except OSError as error:
                print(f'Could not write timing summary: {error}', file=sys.stderr, flush=True)


def main(group=None):
    import json
    with timed(f'Core checks group {group or "full"}'):
        if group in (None, 'unit'):
            updater_tests()
            run([sys.executable, '-B', '-m', 'unittest', 'tooling.ci.test_preview_checks_contract',
                 'tooling.ci.test_preview_shards', '-v'], os.environ.copy())
            run([sys.executable, '-B', '-m', 'unittest', '-v',
                 'tooling.ci.test_cutover_package', 'tooling.ci.test_c528_cutover_package',
                 'tooling.ci.test_c528_rollout', 'tooling.ci.test_maintenance_control'], os.environ.copy())
            run([sys.executable, 'tooling/ci/check_single_runtime.py'], os.environ.copy())
            run([sys.executable, 'services/core-api/tools/check_architecture.py'], os.environ.copy())
        env = os.environ.copy()
        for key in ('PYMENTOR_PROFILE', 'PYMENTOR_DUAL_LOCAL_TEST', 'PYMENTOR_TEST_PG_URL',
                    'PYMENTOR_TEST_REDIS_URL', 'PYMENTOR_REDIS_PREFIX', 'PYMENTOR_LOGIN_RECOVERY_KEY'):
            env.pop(key, None)
        env.update(PYTHONPATH=os.pathsep.join((str(ROOT / 'services/core-api'), str(ROOT))),
                   PYTHON_DOTENV_DISABLED='1', PYTHONDONTWRITEBYTECODE='1', PYMENTOR_SCHEMA_MODE='managed',
                   PYMENTOR_LOCAL_NOTIFICATIONS='0', KNOWI_PG02_INTEGRATION='1',
                   KNOWI_TEST_PG_URL=env.get('KNOWI_TEST_PG_URL', PG),
                   KNOWI_TEST_REDIS_URL=env.get('KNOWI_TEST_REDIS_URL', REDIS),
                   KNOWI_REDIS_PREFIX='preview-ci:', KNOWI_LOGIN_RECOVERY_KEY='42' * 32)
        env['DATABASE_URL'] = env['KNOWI_TEST_PG_URL']
        env['REDIS_URL'] = env['KNOWI_TEST_REDIS_URL']
        if env['REDIS_URL'] != REDIS:
            raise RuntimeError('Disposable Redis target rejected')
        if group != 'unit':
            with timed('Database preparation and Redis readiness'):
                run([sys.executable, 'tooling/ci/prepare_test_databases.py'], env)
                from redis import Redis
                with Redis.from_url(REDIS, socket_connect_timeout=3, socket_timeout=3) as cache:
                    if not cache.ping(): raise RuntimeError('Required Redis unavailable')
        suites = json.loads((ROOT / 'services/core-api/tests/runtime_suites.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory(prefix='core-target-ci-') as temp:
            reports = Path(temp)
            isolated = dict(env, DATABASE_URL='postgresql+psycopg://synthetic:synthetic@127.0.0.1:9/synthetic')
            # These fixtures choose their own database aliases and recovery keys.
            for key in ('KNOWI_REDIS_PREFIX', 'KNOWI_LOGIN_RECOVERY_KEY'):
                isolated.pop(key, None)
            if group in (None, 'unit'):
                with timed('Core unit suites'):
                    pytest_checked(suites['unit'], isolated, reports / 'unit.xml')
                with timed('Core backup guards'):
                    pytest_checked(['tooling/ci/test_c504_backup.py'], isolated, reports / 'backup-guards.xml', prefix='')
            if group in (None, 'runtime'):
                # Delivery includes PostgreSQL-backed course completion fixtures.
                with timed('Core Analytics delivery suites'):
                    pytest_checked(suites['analytics_delivery'], isolated, reports / 'analytics-delivery.xml')
                with timed('Core runtime suite'):
                    pytest_checked(suites['runtime'], env, reports / 'runtime.xml')
            if group is None or group.startswith('http-'):
                from sqlalchemy.engine import make_url
                http_env = dict(env, DATABASE_URL=make_url(env['DATABASE_URL']).set(database='pg08_http_test').render_as_string(hide_password=False))
                files = suites['http'] if group is None else http_partition(suites['http'], group)
                for index, name in enumerate(files):
                    with timed(f'HTTP {name}'):
                        pytest_checked([name], http_env, reports / f'http-{index}.xml')
        if group is None:
            # Existing Runner/security suite remains a required failure-propagating step.
            with timed('Runner contract suite'):
                run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'services/runner-pool/tests', '-v'], env)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=('unit', 'runtime', 'http-1', 'http-2'))
    parser.add_argument('--updater-tests-only', action='store_true')
    args = parser.parse_args()
    if args.updater_tests_only:
        if args.group:
            parser.error('--updater-tests-only cannot be combined with --group')
        updater_tests()
    else:
        main(args.group)
