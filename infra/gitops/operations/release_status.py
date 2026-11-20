"""Read-only post-release checks; run from an authorized operator shell."""
import argparse
import datetime
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class CheckError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise CheckError(message)


def image_matches(pod, container, digest):
    return any(c.get('name') == container and c.get('image', '').endswith('@' + digest)
               for c in pod.get('spec', {}).get('containers', []))


def validate_rollout(obj, digest, container):
    spec, status = obj.get('spec', {}), obj.get('status', {})
    desired = spec.get('replicas', 1)
    require(desired > 0 and status.get('phase') == 'Healthy', 'rollout not Healthy')
    require(str(status.get('observedGeneration')) == str(obj.get('metadata', {}).get('generation')) and obj.get('metadata', {}).get('generation') is not None, 'rollout generation not observed')
    require(status.get('stableRS') and status.get('stableRS') == status.get('currentPodHash'), 'stable and current ReplicaSet differ')
    require(status.get('readyReplicas', 0) == desired and status.get('updatedReplicas', 0) == desired and status.get('replicas', 0) == desired, 'rollout replicas not converged')
    require(image_matches(spec.get('template', {}), container, digest), 'rollout image digest mismatch')


def endpoint_pods(slices, pods, service, digest, container, expected_hash=None):
    by_name = {p['metadata']['name']: p for p in pods.get('items', [])}
    names = set()
    for item in slices.get('items', []):
        require(item.get('metadata', {}).get('labels', {}).get('kubernetes.io/service-name') == service, 'unexpected EndpointSlice')
        for ep in item.get('endpoints', []):
            if ep.get('conditions', {}).get('ready') is not True:
                continue
            ref = ep.get('targetRef', {})
            require(ref.get('kind') == 'Pod' and ref.get('name') in by_name, 'ready endpoint has no matching Pod')
            pod = by_name[ref['name']]
            require(ref.get('uid') == pod.get('metadata', {}).get('uid'), 'endpoint Pod UID mismatch')
            require(not pod.get('metadata', {}).get('deletionTimestamp'), 'endpoint Pod is terminating')
            require(any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in pod.get('status', {}).get('conditions', [])), 'endpoint Pod not Ready')
            require(image_matches(pod, container, digest), 'endpoint image digest mismatch')
            if expected_hash:
                require(pod.get('metadata', {}).get('labels', {}).get('rollouts-pod-template-hash') == expected_hash, 'endpoint belongs to old ReplicaSet')
            names.add(ref['name'])
    require(names, 'no ready endpoints')
    return len(names)


def weighted(obj, stable, canary):
    services = obj.get('spec', {}).get('weighted', {}).get('services', [])
    weights = {s.get('name'): s.get('weight', 1) for s in services}
    require(set(weights) == {stable, canary} and weights[stable] > 0 and weights[canary] == 0, 'traffic is not exclusively stable')


def backup_evidence(path, max_age_hours, now=None):
    """Read existing backup manifests, not the encrypted archive or its keys."""
    paths = sorted(path.glob('knowi-core-*.json')) if path.is_dir() else [path]
    require(paths, 'backup manifests not found')
    now = now or datetime.datetime.now(datetime.timezone.utc)
    candidates = []
    for item_path in paths:
        try:
            item = json.loads(item_path.read_text(encoding='utf-8'))
            identifier = item['id']
            require(isinstance(identifier, str) and re.fullmatch(r'knowi-core-\d{8}T\d{6}Z-[0-9a-f]{8}', identifier), 'invalid backup ID')
            require(item_path.name == identifier + '.json', 'backup manifest name mismatch')
            created = datetime.datetime.fromisoformat(item['created_at'].replace('Z', '+00:00'))
            require(created.tzinfo is not None, 'backup timestamp lacks timezone')
            require(created.astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') == identifier[11:27], 'backup timestamp differs from ID')
            age = (now - created).total_seconds()
            if item.get('server_restore_verified') is True and item.get('offsite_verified') is True and 0 <= age <= max_age_hours * 3600:
                candidates.append((created, identifier))
        except (OSError, ValueError, KeyError, TypeError):
            raise CheckError('invalid or unreadable backup manifest') from None
    require(candidates, 'no fresh backup with server restore and offsite verification')
    created, identifier = max(candidates)
    return identifier, created.isoformat()


def http_get(base, path):
    req = urllib.request.Request(base + path, headers={'User-Agent': 'knowi-release-status/1'})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=8) as response:
            return response.status, response.headers, response.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, b''
    except (OSError, ValueError):
        raise CheckError('HTTP request failed: ' + path) from None


def smoke(base, revision, frontend_revision=None):
    if frontend_revision:
        status, _, body = http_get(base, '/release-revision.txt')
        require(status == 200 and body.strip() == frontend_revision.encode('ascii'), 'frontend revision mismatch')
    for path, expected in [('/', 200), ('/health/ready', 200), ('/api/learning/catalog', 200), ('/api/user/me', 401), ('/api/user/booking-mode', 401)]:
        status, headers, body = http_get(base, path)
        require(status == expected, f'{path}: HTTP {status}, expected {expected}')
        if path == '/':
            require(b'<html' in body.lower() and 'text/html' in headers.get('Content-Type', '').lower(), 'root is not HTML')
            asset = re.search(rb'''(?:src|href)=["'](/assets/[^"']+\.(?:js|css))["']''', body)
            require(asset is not None, 'frontend asset reference missing')
            asset_status, asset_headers, asset_body = http_get(base, asset.group(1).decode('ascii'))
            require(asset_status == 200 and bool(asset_body) and 'text/html' not in asset_headers.get('Content-Type', '').lower(), 'frontend asset unavailable')
        if path.startswith('/health/') or path.startswith('/api/'):
            require(headers.get('X-Pymentor-Revision') == revision, path + ': revision header mismatch')
        if path == '/api/learning/catalog':
            try:
                result = json.loads(body)
                require(isinstance(result, (dict, list)) and bool(result), 'catalog JSON empty')
            except (ValueError, UnicodeError):
                raise CheckError('catalog is not JSON') from None


def verify_schema(service, expected):
    """Use an operator-provided PostgreSQL service and never print its connection errors."""
    query = 'BEGIN READ ONLY; SELECT current_database(), version_num FROM public.alembic_version; COMMIT;'
    command = ['psql', '-XqAtw', '-v', 'ON_ERROR_STOP=1', '-d', 'service=' + service, '-c', query]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
    except (subprocess.SubprocessError, OSError):
        raise CheckError('could not read Core PostgreSQL schema from the selected service') from None
    require(result.stdout.strip() == 'knowi_core|' + expected, 'unexpected Core database name or schema revision')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-file', type=Path, required=True)
    parser.add_argument('--public-only', action='store_true', help='only public read-only smoke checks')
    parser.add_argument('--gitops-sha')
    parser.add_argument('--context')
    parser.add_argument('--namespace')
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--kubectl', help='kubectl executable, or k3s executable with --k3s')
    parser.add_argument('--k3s', action='store_true')
    parser.add_argument('--argo-namespace', default='argocd-preview')
    parser.add_argument('--application', default='pymentor-preview')
    parser.add_argument('--backup-index', type=Path, help='backup manifest or directory containing manifests')
    parser.add_argument('--max-backup-age-hours', type=float, default=24)
    parser.add_argument('--pg-service', help='local PostgreSQL service name for the Core database')
    parser.add_argument('--expected-schema', help='expected Core Alembic revision, used with --pg-service')
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--interval', type=int, default=10)
    args = parser.parse_args(argv)
    try:
        if not args.public_only:
            require(bool(args.context and args.namespace), 'context and namespace are required')
            require(isinstance(args.gitops_sha, str) and SHA.fullmatch(args.gitops_sha), 'gitops SHA must be full lowercase SHA')
        require(1 <= args.timeout <= 1800 and 1 <= args.interval <= args.timeout, 'invalid timeout or interval')
        require(0 < args.max_backup_age_hours <= 8760, 'invalid backup maximum age')
        if args.pg_service or args.expected_schema:
            require(not args.public_only and all(isinstance(v, str) and re.fullmatch(r'[\w-]{1,80}', v, re.ASCII)
                        for v in (args.pg_service, args.expected_schema)), 'set both PostgreSQL options in cluster mode')
        url = urllib.parse.urlsplit(args.base_url)
        require(url.scheme == 'https' and url.hostname and not url.username and not url.password and not url.query and not url.fragment and url.path in ('', '/'), 'base URL must be an HTTPS origin')
        base = args.base_url.rstrip('/')
        release = json.loads(args.release_file.read_text(encoding='utf-8'))
        require(isinstance(release, dict), 'release must be an object')
        source = release.get('source_sha')
        sources = release.get('image_sources')
        require(isinstance(source, str) and SHA.fullmatch(source), 'invalid release source SHA')
        if sources is None:
            revision, frontend_revision = source, None
        else:
            require(isinstance(sources, dict) and set(sources) == {'core', 'frontend', 'runner-pool', 'core-migrations', 'analytics'}
                    and all(isinstance(sha, str) and SHA.fullmatch(sha) for sha in sources.values()), 'invalid image sources')
            revision, frontend_revision = sources['core'], sources['frontend']
        for key in ('core_digest', 'frontend_digest', 'runner_digest'):
            require(isinstance(release.get(key), str) and DIGEST.fullmatch(release[key]), 'invalid ' + key)
    except (OSError, ValueError, TypeError, CheckError) as exc:
        print('ERROR: invalid input:', str(exc), file=sys.stderr)
        return 2
    command = [args.kubectl or ('k3s' if args.k3s else 'kubectl')]
    if args.k3s:
        command.append('kubectl')
    command += ['--context', args.context, '--request-timeout=8s']

    def get(resource, name=None, namespace=None, selector=None):
        cmd = command + ['-n', namespace or args.namespace, 'get', resource]
        if name:
            cmd.append(name)
        if selector:
            cmd += ['-l', selector]
        cmd += ['-o', 'json']
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
            return json.loads(result.stdout)
        except (subprocess.SubprocessError, OSError, ValueError):
            raise CheckError('unable to read ' + resource + (('/' + name) if name else '')) from None

    def check():
        evidence = backup_evidence(args.backup_index, args.max_backup_age_hours) if args.backup_index else None
        if args.public_only:
            smoke(base, revision, frontend_revision)
            return evidence
        app = get('applications.argoproj.io', args.application, args.argo_namespace)
        status = app.get('status', {})
        require(status.get('sync', {}).get('status') == 'Synced' and status.get('health', {}).get('status') == 'Healthy', 'Argo Application not Synced/Healthy')
        require(status.get('sync', {}).get('revision') == args.gitops_sha, 'Argo GitOps revision mismatch')
        require(not status.get('operationState', {}).get('phase') in ('Running', 'Terminating'), 'Argo operation still running')
        for name, service, container, key in [('core-api-progressive', 'core-api', 'core-api', 'core_digest'), ('frontend-progressive', 'frontend', 'frontend', 'frontend_digest')]:
            rollout = get('rollouts.argoproj.io', name)
            validate_rollout(rollout, release[key], container)
            weighted(get('traefikservices.traefik.io', service + '-weighted'), service, service + '-canary')
            endpoint_pods(get('endpointslices.discovery.k8s.io', selector='kubernetes.io/service-name=' + service), get('pods', selector='app=' + name), service, release[key], container, rollout['status']['stableRS'])
        deployment = get('deployments.apps', 'preview-pool-broker')
        ds, st = deployment.get('spec', {}), deployment.get('status', {})
        n = ds.get('replicas', 1)
        require(n > 0 and st.get('observedGeneration') == deployment.get('metadata', {}).get('generation') and st.get('readyReplicas', 0) == n and st.get('updatedReplicas', 0) == n and st.get('replicas', 0) == n, 'runner Deployment not converged')
        require(image_matches(ds.get('template', {}), 'broker', release['runner_digest']), 'runner image mismatch')
        endpoint_pods(get('endpointslices.discovery.k8s.io', selector='kubernetes.io/service-name=preview-runner-broker'), get('pods', selector='app=preview-pool-broker'), 'preview-runner-broker', release['runner_digest'], 'broker')
        cron = get('cronjobs.batch', 'notifications')
        require(cron.get('spec', {}).get('suspend') is not True, 'notifications CronJob suspended')
        containers = cron.get('spec', {}).get('jobTemplate', {}).get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
        require(any(c.get('name') == 'worker' and c.get('image', '').endswith('@' + release['core_digest']) for c in containers), 'notifications image mismatch')
        if args.pg_service:
            verify_schema(args.pg_service, args.expected_schema)
        smoke(base, revision, frontend_revision)
        return evidence

    deadline = time.monotonic() + args.timeout
    while True:
        try:
            evidence = check()
            print('Release candidate source:', source)
            print('Observed public Core source:', revision)
            if frontend_revision:
                print('Observed public Web source:', frontend_revision)
            else:
                print('NOT VERIFIED: public Web source for this legacy release entry.')
            if evidence:
                print('BACKUP metadata:', evidence[0], evidence[1], '(manifest assertions only; archive not decrypted or restored again)')
            else:
                print('NOT VERIFIED: backup restore/offsite evidence; no backup manifest supplied.')
            if args.public_only:
                print('PASS: public HTML and asset, readiness, catalog and anonymous access smoke')
                print('NOT VERIFIED: Argo GitOps revision, Rollouts, stable traffic, endpoints, runner and notifications.')
            else:
                print('Observed Argo GitOps revision:', args.gitops_sha)
                print('PASS: Argo revision, Rollouts, stable traffic, endpoints, runner, notifications configuration and public read-only smoke')
            if args.pg_service:
                print('PASS: Core PostgreSQL schema', args.expected_schema)
            else:
                print('NOT VERIFIED: actual DB migration revision. Readiness checks compatibility only.')
            print('NOT VERIFIED: booking mode value requires authenticated access.')
            print('NOT VERIFIED: login, writes, task execution, notifications delivery, and end-to-end functional behavior.')
            return 0
        except CheckError as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print('FAIL:', str(exc), file=sys.stderr)
                print('NOT VERIFIED: DB revision and write/functional behavior.', file=sys.stderr)
                return 1
            print('WAIT:', str(exc), flush=True)
            time.sleep(min(args.interval, remaining))


if __name__ == '__main__':
    sys.exit(main())
