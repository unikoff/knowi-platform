"""Compute a conservative, read-only release plan from Git history."""
import argparse
import json
import re
import subprocess

IMAGES = ('core', 'core-migrations', 'frontend', 'runner-pool', 'analytics')
DIGEST_FIELDS = dict(zip(IMAGES, ('core_digest', 'migration_digest', 'frontend_digest', 'runner_digest', 'analytics_digest')))
RELEASE = 'infra/gitops/preview/release.json'
PUBLISHED_FILES = {'infra/gitops/preview/release.json', 'infra/gitops/preview/applications.json',
                   'infra/gitops/preview/kustomization.yaml', 'infra/gitops/preview/runner.json'}
SHA = re.compile(r'[0-9a-f]{40}\Z')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')


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
                core_rehearsal=True, analytics_rehearsal=True,
                deploy=True, reasons=[why])


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


def classify(path):
    """Only proven independent paths are selective; all other paths go full."""
    if path.startswith('apps/web/'):
        # Web runtime code and its build/configuration can change the analytics client.
        presentation = path.endswith(('.css', '.svg', '.png', '.webp', '.jpg', '.woff', '.woff2', '.md'))
        return {'frontend'}, not presentation, False, False, True
    if path.startswith('courses/') and '/frontend/' in path:
        return {'core', 'core-migrations', 'frontend'}, False, False, False, True
    if path.startswith('courses/'):
        # Both Dockerfiles copy courses. Analytics' generated Core fixture copies
        # the shared backend, so exercise its contracts on non-presentation changes.
        content_only = '/backend/plans/' in path and path.endswith('.md')
        return {'core', 'core-migrations', 'frontend'}, not content_only, False, False, True
    if path.startswith('services/runner-pool/'):
        if path.startswith('services/runner-pool/tests/') or path.endswith('.md'):
            return set(), False, False, False, False
        return {'runner-pool'}, False, False, False, True
    if path.startswith('services/core-api/'):
        relative = path.removeprefix('services/core-api/')
        if relative.startswith('tests/') or relative.endswith('.md'):
            return set(), True, False, False, False
        if relative.startswith(('migrations/', 'alembic', 'requirements-migration.txt', 'tools/analytics_transfer/')):
            return {'core-migrations'}, True, True, True, True
        database = relative.startswith((
            'requirements', 'Dockerfile', 'pymentor_core/adapters/postgres/', 'pymentor_core/adapters/sql/',
            'pymentor_core/app/schema_policy.py', 'pymentor_core/app/database',
        ))
        analytics_runtime = database or 'analytics_delivery' in path or relative.startswith('contracts/')
        # The cross-service fixture also starts Core, so keep its ordinary checks.
        return {'core', 'core-migrations'}, True, database, analytics_runtime, True
    if path.startswith('services/analytics/'):
        relative = path.removeprefix('services/analytics/')
        if relative.startswith(('tests/', 'tools/', 'bench/')) or relative.endswith('.md'):
            return set(), True, False, not relative.endswith('.md'), False
        return {'analytics'}, True, False, True, True
    if path.startswith(('infra/gitops/analytics/', 'infra/analytics-local/', 'infra/helm/analytics/')):
        return set(), True, False, True, True
    if path.startswith('docs/') and path.endswith('.md') and not path.startswith('docs/multicourse/'):
        return set(), False, False, False, False
    return set(IMAGES), True, True, True, True


def check_scope(path):
    """Select Core and Web gates; unrecognised paths require both.

    The always-run workflow gate covers documentation-only changes. Keep this
    independent of image selection: tests may need a gate without a rebuild.
    """
    if path.startswith('apps/web/'):
        presentation = path.endswith(('.css', '.svg', '.png', '.webp', '.jpg', '.woff', '.woff2', '.md'))
        return not presentation, True
    if path.startswith('courses/'):
        return True, True
    if path.startswith('services/runner-pool/'):
        return True, False
    if path.startswith('services/core-api/'):
        relative = path.removeprefix('services/core-api/')
        # Runtime changes may affect public APIs consumed by Web. Tests and
        # migration-only files still need Core, but do not change that API.
        core_only = relative.startswith(('tests/', 'migrations/', 'alembic', 'requirements-migration.txt',
                                         'tools/analytics_transfer/')) or relative.endswith('.md')
        return True, not core_only
    if path.startswith(('services/analytics/', 'infra/gitops/analytics/',
                        'infra/analytics-local/', 'infra/helm/analytics/')):
        return False, False
    if path.startswith('docs/') and path.endswith('.md') and not path.startswith('docs/multicourse/'):
        return False, False
    return True, True


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
        builds, analytics, core_rehearsal, analytics_rehearsal, deploy = set(), False, False, False, False
        core_checks, web_checks = False, False
        for path in paths:
            images, a, c, ar, d = classify(path)
            core, web = check_scope(path)
            builds.update(images)
            core_checks |= core
            web_checks |= web
            analytics |= a
            core_rehearsal |= c
            analytics_rehearsal |= ar
            deploy |= d
        reuse = {name: digests[name] for name in IMAGES if name not in builds} if mode == 'push' else {}
        image_sources = {name: (sources[name] if name in reuse else head) for name in IMAGES}
        return dict(build=[name for name in IMAGES if name in builds], reuse=reuse,
                    image_sources=image_sources, core_checks=core_checks, web_checks=web_checks,
                    analytics_checks=analytics,
                    core_rehearsal=core_rehearsal, analytics_rehearsal=analytics_rehearsal,
                    deploy=deploy or bool(builds),
                    reasons=[f'{len(paths)} changed paths from {source} to {head}', *paths[:30]])
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
