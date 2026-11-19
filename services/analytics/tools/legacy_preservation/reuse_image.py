"""One copy-only smoke using the reconciled image from legacy run 107c.

No build, retry or automatic cleanup. Requires an explicit separate run GO.
"""
import argparse
import copy
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from uuid import UUID

spec = importlib.util.spec_from_file_location('legacy_runner', Path(__file__).resolve().parents[1] / 'test_legacy_preservation.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

SOURCE_HEX = '107c271245984a269e2c622834061e3e'
SOURCE_ID = '107c2712-4598-4a26-9e2c-622834061e3e'
IMAGE = 'sha256:7e7ca35bcfc38495159b77be2b7753a31825c373d70f2c21b606fc249386ed92'
OLD_CONTAINER = '1b81b2c8e468fee76fcdf7f90633cdc091cc6ba28da8f8d6abbfd6359d6de706'
DOCKER = Path(r'C:\Users\Unik\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe')
DOCKER_SHA = '17cbf2f5f5f7693bbeda46e35dc930050b40aff06f41f9f5a69e4e2dd7fc12aa'
ENDPOINT = 'npipe:////./pipe/dockerDesktopLinuxEngine'


def validate_mounts(obj, container, image, data, runtime):
    mounts = obj.get('Mounts', [])
    if len(mounts) != 2:
        raise ValueError('container isolation mismatch')
    runtime_mounts = [m for m in mounts if m.get('Destination') == '/smoke_runtime.py']
    if (len(runtime_mounts) != 1 or runtime_mounts[0].get('Type') != 'bind'
            or runtime_mounts[0].get('RW') is not False
            or os.path.normcase(os.path.normpath(runtime_mounts[0].get('Source', ''))) != os.path.normcase(os.path.normpath(str(runtime)))):
        raise ValueError('container isolation mismatch')
    restricted = copy.deepcopy(obj)
    restricted['Mounts'] = [m for m in mounts if m.get('Destination') == '/data']
    r.validate_container(restricted, container, image, data)


def run(run_id):
    if os.name != 'nt' or os.environ.get('LEGACY_PRESERVATION_REUSE_GO') != '1' or run_id.version != 4:
        raise ValueError('explicit isolated run GO required')
    source = r.RUN_ROOT / SOURCE_HEX
    config = source / 'docker-config'
    if source.resolve() != source.absolute() or source.is_symlink() or config.is_symlink():
        raise ValueError('source run identity mismatch')
    if r.sha(DOCKER) != DOCKER_SHA or (config / 'config.json').read_bytes() != b'{"auths":{},"proxies":{}}':
        raise ValueError('Docker identity mismatch')
    read = lambda name: json.loads((source / name).read_text(encoding='utf-8'))
    owner, build, diagnostics = read('owner.json'), read('build-confirmed.json'), read('build-diagnostics.json')
    receipt, old = read('build-pending.json'), read('create-confirmed.json')
    if (owner.get('run_id') != SOURCE_ID or owner.get('commit') != r.COMMIT or build.get('image') != IMAGE
            or diagnostics.get('outcome') != 'cli_success' or receipt.get('commit') != r.COMMIT
            or old.get('container') != OLD_CONTAINER):
        raise ValueError('source image receipt mismatch')
    before = r.source_observations()
    if before != read('source-after.json').get('source_after'):
        raise ValueError('source observations mismatch')
    r.check_checkout()
    env = r.docker_environment(os.environ)
    if r.local_docker(DOCKER, 'legacy-smoke', env, config_dir=config) != ENDPOINT:
        raise ValueError('Docker endpoint mismatch')
    dc = [str(DOCKER), '--config', str(config), '--context', 'legacy-smoke']
    prior = json.loads(r.call(dc + ['inspect', '--type', 'container', OLD_CONTAINER], env=env))[0]
    if (prior.get('Id') != OLD_CONTAINER or prior.get('Image') != IMAGE
            or prior['State']['Status'] != 'exited' or prior['State']['ExitCode'] != 1 or prior['State']['OOMKilled']):
        raise ValueError('previous container not reconciled')
    if json.loads(r.call(dc + ['image', 'inspect', IMAGE], env=env))[0].get('Id') != IMAGE:
        raise ValueError('image mismatch')
    runtime_hash, runner_hash = r.sha(r.RUNTIME), r.sha(Path(__file__))
    folder = r.prepare_dir(run_id)
    r.mark(folder, 'owner', {'run_id': str(run_id), 'source_run': SOURCE_ID, 'image': IMAGE,
                             'runtime_sha256': runtime_hash, 'reuse_runner_sha256': runner_hash, 'source_before': before})
    try:
        data = folder / 'data'
        data.mkdir()
        (data / 'state').mkdir()
        with r.ORIGINAL.open('rb') as src:
            r.write_new(data / 'test.db', src.read(r.SIZE + 1))
        if (data / 'test.db').stat().st_size != r.SIZE or r.sha(data / 'test.db') != r.EXPECTED:
            raise ValueError('copy mismatch')
        runtime = folder / 'smoke_runtime.py'
        r.write_new(runtime, r.RUNTIME.read_bytes())
        if r.sha(runtime) != runtime_hash or r.sha(r.RUNTIME) != runtime_hash:
            raise ValueError('runtime changed')
        r.mark(folder, 'create-pending', {'image': IMAGE, 'runtime_sha256': runtime_hash})
        args = dc + ['create', '--name', 'analytics-legacy-reuse-' + run_id.hex, '--network', 'none',
                     '--user', '10001:10001', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                     '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m', '--pids-limit', '128',
                     '--memory', '512m', '--cpus', '1', '--mount', f'type=bind,source={data},target=/data',
                     '--mount', f'type=bind,source={runtime},target=/smoke_runtime.py,readonly',
                     '--label', 'knowi.legacy-reuse=' + run_id.hex,
                     '--env', 'PYMENTOR_PROFILE=legacy_sqlite', '--env', 'DATABASE_URL=sqlite:////data/test.db',
                     '--env', 'PYTHON_DOTENV_DISABLED=1', '--env', 'PYMENTOR_LOCAL_NOTIFICATIONS=0',
                     '--env', 'PYMENTOR_STATE_DIR=/data/state', IMAGE]
        container = r.docker_operation(folder, 'create', args, env=env, timeout=30).decode('ascii').strip()
        if not re.fullmatch(r'[0-9a-f]{64}', container):
            raise ValueError('container identity unavailable')
        r.mark(folder, 'create-confirmed', {'container': container})
        obj = json.loads(r.call(dc + ['inspect', '--type', 'container', container], env=env))[0]
        validate_mounts(obj, container, IMAGE, data, runtime)
        if r.sha(runtime) != runtime_hash or r.sha(r.RUNTIME) != runtime_hash or r.sha(Path(__file__)) != runner_hash:
            raise ValueError('runtime changed')
        if r.local_docker(DOCKER, 'legacy-smoke', env, config_dir=config) != ENDPOINT:
            raise ValueError('Docker endpoint changed')
        r.mark(folder, 'start-pending', {'container': container})
        raw = r.docker_operation(folder, 'start', dc + ['start', '--attach', container], env=env, timeout=120)
        r.mark(folder, 'wait-pending', {'container': container})
        if r.docker_operation(folder, 'wait', dc + ['wait', container], env=env, timeout=15).strip() != b'0':
            raise ValueError('container exit not successful')
        result = json.loads(r._safe_diagnostic(raw, 'start'))
        if result.get('status') != 'PASS':
            raise ValueError('smoke result unavailable')
        r.mark(folder, 'wait-confirmed', {'container': container, 'exit': 0})
        r.mark(folder, 'start-confirmed', {'container': container, 'exit': 0})
    finally:
        after = r.source_observations()
        r.mark(folder, 'source-after', {'source_after': after, 'unchanged': after == before})
        if after != before:
            raise ValueError('source changed')
    r.mark(folder, 'completed', {'result': result, 'image': IMAGE, 'container': container})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    run_id = UUID(args.run_id)
    if str(run_id) != args.run_id:
        raise ValueError('canonical run identity required')
    print(json.dumps(run(run_id), separators=(',', ':')))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Legacy reuse stopped or outcome unknown; preserve state and reconcile.', file=sys.stderr)
        raise SystemExit(2) from None
