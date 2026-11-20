"""Compute a conservative, read-only release plan from Git history."""
import argparse
import json
import re
import subprocess
from typing import NamedTuple

IMAGES = ('core', 'core-migrations', 'frontend', 'runner-pool', 'analytics')
DIGEST_FIELDS = dict(zip(IMAGES, ('core_digest', 'migration_digest', 'frontend_digest', 'runner_digest', 'analytics_digest')))
RELEASE = 'infra/gitops/preview/release.json'
PUBLISHED_FILES = {'infra/gitops/preview/release.json', 'infra/gitops/preview/applications.json',
                   'infra/gitops/preview/kustomization.yaml', 'infra/gitops/preview/runner.json'}
SHA = re.compile(r'[0-9a-f]{40}\Z')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')
SERVICE_CHECKS = ('core', 'web', 'analytics', 'runner')
CORE_IMAGES = ('core', 'core-migrations')
CORE_WEB = ('core', 'web')
CORE_WEB_ANALYTICS = (*CORE_WEB, 'analytics')
CORE_WEB_RUNNER = (*CORE_WEB, 'runner')
WEB_PRESENTATION = ('.css', '.svg', '.png', '.webp', '.jpg', '.woff', '.woff2', '.md')


class Scope(NamedTuple):
    reason: str
    checks: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    core_rehearsal: bool = False
    analytics_rehearsal: bool = False
    deploy: bool = False


def git(*args):
    return subprocess.check_output(['git', *args], stderr=subprocess.PIPE)


def revision(ref):
    value = git('rev-parse', '--verify', '--end-of-options', ref + '^{commit}').decode('ascii').strip()
    if not SHA.fullmatch(value):
        raise ValueError('invalid commit SHA')
    return value


def ancestor(older, newer):
    return subprocess.run(['git', 'merge-base', '--is-ancestor', older, newer],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def full(head, why):
    return dict(build=list(IMAGES), reuse={}, image_sources={name: head for name in IMAGES},
                core_checks=True, web_checks=True, analytics_checks=True,
                runner_checks=True, core_rehearsal=True, analytics_rehearsal=True,
                deploy=True, reasons=[why], check_reasons=dict.fromkeys(SERVICE_CHECKS, why))


def baseline(ref, head):
    ref = revision(ref)
    if not ancestor(ref, head):
        raise ValueError('baseline ref is not an ancestor of head')
    document = json.loads(git('show', f'{ref}:{RELEASE}').decode('utf-8'))
    if not isinstance(document, dict):
        raise ValueError('release metadata is not an object')
    source = document.get('source_sha')
    if not isinstance(source, str) or not SHA.fullmatch(source) or not ancestor(source, ref):
        raise ValueError('release source is invalid or not an ancestor of baseline')
    last = git('log', '-1', '--format=%H', ref, '--', RELEASE).decode('ascii').strip()
    fields = git('show', '-s', '--format=%P%n%ae%n%s', last).decode('utf-8').splitlines()
    if fields != [source, '41898282+github-actions[bot]@users.noreply.github.com',
                  'chore(preview): update image digests [skip ci]']:
        raise ValueError('release metadata is not from the expected publication commit')
    digests = {}
    for name, field in DIGEST_FIELDS.items():
        value = document.get(field)
        if not isinstance(value, str) or not DIGEST.fullmatch(value):
            raise ValueError(f'missing or invalid {field}')
        digests[name] = value
    sources = document.get('image_sources')
    if sources is None:
        sources = {name: source for name in IMAGES}
    if not isinstance(sources, dict) or set(sources) != set(IMAGES):
        raise ValueError('incomplete image_sources')
    for value in sources.values():
        if not isinstance(value, str) or not SHA.fullmatch(value) or not ancestor(value, source):
            raise ValueError('invalid image source or source is not an ancestor')
    return source, digests, sources, last


def web_scope(path):
    images = ('frontend',)
    if path.endswith(WEB_PRESENTATION) or path.startswith(('src/shared/ui/', 'src/shared/brand/')) or path in {
        'src/app/StartupScreen.tsx', 'src/app/NotFoundPage.tsx',
    }:
        return Scope('Web presentation', ('web',), images)
    if path == 'src/features/learning/api.ts':
        return Scope('Web client of Core and Runner', CORE_WEB_RUNNER, images)
    if path.startswith('src/shared/api/contracts/') or path in {
        'src/features/auth/api.ts', 'src/features/admin/api.ts', 'src/features/admin/scopedApi.ts',
    }:
        return Scope('Web client of Core', CORE_WEB, images)
    return Scope('Web integration or unclassified Web runtime', CORE_WEB_ANALYTICS, images)


def core_scope(path):
    if path.startswith('tests/') or path.endswith('.md'):
        return Scope('Core tests or documentation', ('core',))
    if path.startswith(('migrations/', 'alembic', 'requirements-migration.txt', 'tools/analytics_transfer/')):
        return Scope('Core migration or Analytics transfer', ('core', 'analytics'), ('core-migrations',),
                     core_rehearsal=True, analytics_rehearsal=True)
    relative = path.removeprefix('pymentor_core/')
    database = path.startswith(('requirements', 'Dockerfile')) or relative.startswith((
        'adapters/postgres/', 'adapters/sql/', 'app/schema_policy.py', 'app/database',
    ))
    if database:
        return Scope('Core database or runtime dependency', CORE_WEB_ANALYTICS, CORE_IMAGES,
                     core_rehearsal=True, analytics_rehearsal=True)
    if path.startswith('contracts/') or 'analytics' in relative:
        return Scope('Core and Analytics integration', CORE_WEB_ANALYTICS, CORE_IMAGES)
    if relative.startswith(('adapters/legacy_runner/', 'modules/practice/')):
        return Scope('Core execution integration', CORE_WEB_RUNNER, CORE_IMAGES)
    if relative == 'modules/learning/api/service.py':
        return Scope('Core learning service and remote Runner client', SERVICE_CHECKS, CORE_IMAGES)
    if relative.startswith('adapters/telegram/') or relative in {
        'modules/notifications/policy.py', 'modules/notifications/review_policy.py',
    }:
        return Scope('Private Core notification implementation', ('core',), CORE_IMAGES)
    if relative.startswith(('modules/identity/api/', 'modules/learning/api/', 'app/', 'shared/')):
        return Scope('Core runtime shared with Web and Analytics', CORE_WEB_ANALYTICS, CORE_IMAGES)
    return Scope('Core runtime consumed by Web', CORE_WEB, CORE_IMAGES)


def analytics_scope(path):
    if path.endswith('.md'):
        return Scope('Analytics documentation', ('analytics',))
    if path.startswith(('tests/', 'tools/', 'bench/')):
        return Scope('Analytics tests, tooling or benchmark', ('analytics',), analytics_rehearsal=True)
    images = ('analytics',)
    if path.startswith(('migrations/', 'alembic', 'requirements', 'Dockerfile')):
        return Scope('Analytics database or runtime dependency', ('analytics',), images, analytics_rehearsal=True)
    relative = path.removeprefix('pymentor_analytics/')
    if path.startswith('contracts/') or '/api/' in relative or relative.startswith((
        'contracts/', 'adapters/auth/', 'adapters/core_privacy/', 'adapters/privacy_authority/',
        'modules/analytics/core_transfer/', 'app/core_transfer_runtime.py', 'app/factory.py', 'app/config.py',
    )):
        return Scope('Analytics contract consumed by Core or Web', CORE_WEB_ANALYTICS, images,
                     analytics_rehearsal=True)
    if relative == 'app/telemetry.py':
        return Scope('Private Analytics instrumentation', ('analytics',), images)
    return Scope('Analytics runtime', ('analytics',), images, analytics_rehearsal=True)


def classify(path):
    """Select suites and images from repository ownership and contract boundaries."""
    if path.startswith('apps/web/'):
        return web_scope(path.removeprefix('apps/web/'))
    if path.startswith('courses/') and '/frontend/' in path:
        return Scope('Course frontend copied by Core and Web', CORE_WEB, (*CORE_IMAGES, 'frontend'))
    if path.startswith('courses/'):
        content_only = '/backend/plans/' in path and path.endswith('.md')
        checks = CORE_WEB if content_only else CORE_WEB_ANALYTICS
        return Scope('Shared course content and runtime', checks, (*CORE_IMAGES, 'frontend'))
    if path.startswith('services/runner-pool/'):
        runner_path = path.removeprefix('services/runner-pool/')
        if runner_path.startswith('tests/') or runner_path.endswith('.md'):
            return Scope('Runner tests or documentation', ('runner',))
        if runner_path == 'pool_manager.py':
            return Scope('Private Runner pool manager', ('runner',), ('runner-pool',))
        return Scope('Runner execution boundary', CORE_WEB_RUNNER, ('runner-pool',))
    if path.startswith('services/core-api/'):
        return core_scope(path.removeprefix('services/core-api/'))
    if path.startswith('services/analytics/'):
        return analytics_scope(path.removeprefix('services/analytics/'))
    if path.startswith(('infra/gitops/analytics/', 'infra/analytics-local/', 'infra/helm/analytics/')):
        return Scope('Analytics deployment configuration', ('analytics',), analytics_rehearsal=True, deploy=True)
    if path.startswith('docs/') and path.endswith('.md') and not path.startswith('docs/multicourse/'):
        return Scope('Project documentation')
    return Scope('Shared infrastructure or unknown ownership', SERVICE_CHECKS, IMAGES,
                 core_rehearsal=True, analytics_rehearsal=True)


def plan(mode, head_ref, baseline_ref=None, base_ref=None, force=False):
    try:
        head = revision(head_ref)
        if force:
            return full(head, 'full override')
        if mode == 'push':
            if not baseline_ref:
                raise ValueError('missing baseline ref')
            source, digests, sources, published = baseline(baseline_ref, head)
        elif mode == 'pr':
            if not base_ref:
                raise ValueError('missing PR base ref')
            source = revision(base_ref)
            if not ancestor(source, head):
                raise ValueError('PR base is not an ancestor of head')
            digests, sources = {}, {}
        else:
            raise ValueError('unknown mode')
        paths_raw = git('diff', '--no-renames', '--name-only', '-z', source, head, '--')
        paths = [part.decode('utf-8', errors='strict') for part in paths_raw.split(b'\0') if part]
        if mode == 'push':
            changed_since_publication = {
                part.decode('utf-8', errors='strict') for part in
                git('diff', '--no-renames', '--name-only', '-z', published, head, '--').split(b'\0') if part
            }
            # Compare generated manifests against the bot commit, even if the
            # candidate overwrote a file back to the pre-publication version.
            paths = sorted((set(paths) - PUBLISHED_FILES) | (changed_since_publication & PUBLISHED_FILES))
        builds, checks = set(), set()
        core_rehearsal = analytics_rehearsal = deploy = False
        reasons = [f'{len(paths)} changed paths from {source} to {head}']
        selection = {}
        for path in paths:
            scope = classify(path)
            builds.update(scope.images)
            checks.update(scope.checks)
            core_rehearsal |= scope.core_rehearsal
            analytics_rehearsal |= scope.analytics_rehearsal
            deploy |= scope.deploy
            for service in scope.checks:
                selection.setdefault(service, f'{path}: {scope.reason}')
            if len(reasons) <= 30:
                reasons.append(f'{path}: {scope.reason}')
        reuse = {name: digests[name] for name in IMAGES if name not in builds} if mode == 'push' else {}
        image_sources = {name: (sources[name] if name in reuse else head) for name in IMAGES}
        return dict(build=[name for name in IMAGES if name in builds], reuse=reuse,
                    image_sources=image_sources,
                    **{f'{service}_checks': service in checks for service in SERVICE_CHECKS},
                    core_rehearsal=core_rehearsal, analytics_rehearsal=analytics_rehearsal,
                    deploy=deploy or bool(builds),
                    reasons=reasons, check_reasons=selection)
    except (ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError, subprocess.SubprocessError, OSError) as exc:
        return full(head_ref, f'conservative fallback: {exc}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('push', 'pr'), required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--baseline-ref')
    parser.add_argument('--base-ref')
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    print(json.dumps(plan(args.mode, args.head, args.baseline_ref, args.base_ref, args.full)))


if __name__ == '__main__':
    main()
