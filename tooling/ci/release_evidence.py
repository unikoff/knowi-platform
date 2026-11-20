"""Validate release gates and image evidence before publication."""
import argparse
import json
import os
from pathlib import Path
import re
import sys


IMAGES = ('core', 'core-migrations', 'frontend', 'runner-pool', 'analytics')
SHA = re.compile(r'[0-9a-f]{40}\Z')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')


def plan_from_env():
    plan = json.loads(os.environ['PLAN'])
    if not isinstance(plan, dict):
        raise ValueError('PLAN must be an object')
    return plan


def check_gates(plan):
    flags = ('core_checks', 'core_rehearsal', 'web_checks', 'runner_checks', 'analytics_checks', 'analytics_rehearsal')
    for flag in flags:
        if type(plan.get(flag)) is not bool:
            raise ValueError(f'PLAN {flag} must be a boolean')
    gates = (
        ('CORE_RESULT', plan['core_checks'] or plan['core_rehearsal']),
        ('WEB_RESULT', plan['web_checks']),
        ('RUNNER_RESULT', plan['runner_checks']),
        ('ANALYTICS_RESULT', plan['analytics_checks']),
        ('ANALYTICS_REHEARSAL_RESULT', plan['analytics_rehearsal']),
    )
    errors = []
    for variable, selected in gates:
        result = os.environ.get(variable, '')
        expected = 'success' if selected else 'skipped'
        if result != expected:
            errors.append(f'{variable}={result or "<missing>"}, expected {expected}')
    if errors:
        raise ValueError('; '.join(errors))
    print('Release gates validated')


def collect(plan, directory):
    candidate = os.environ['GITHUB_SHA']
    run_id = os.environ['GITHUB_RUN_ID']
    output = os.environ['GITHUB_OUTPUT']
    if not SHA.fullmatch(candidate) or not run_id or not output:
        raise ValueError('invalid candidate SHA, run ID, or output path')
    builds, reuse, sources = plan.get('build'), plan.get('reuse'), plan.get('image_sources')
    if (not isinstance(builds, list) or any(not isinstance(x, str) for x in builds)
            or len(builds) != len(set(builds)) or not set(builds) <= set(IMAGES)):
        raise ValueError('invalid build list')
    if (not isinstance(reuse, dict) or set(reuse) != set(IMAGES) - set(builds)
            or not isinstance(sources, dict) or set(sources) != set(IMAGES)):
        raise ValueError('incomplete reuse or image_sources')
    digests = {}
    for name in IMAGES:
        source = sources[name]
        if not isinstance(source, str) or not SHA.fullmatch(source):
            raise ValueError(f'{name}: invalid planned source SHA')
        if name in builds and source != candidate:
            raise ValueError(f'{name}: built image source must match candidate')
        record = json.loads((Path(directory) / f'release-image-{name}' / 'image.json').read_text(encoding='utf-8'))
        if not isinstance(record, dict):
            raise ValueError(f'{name}: record must be an object')
        digest = record.get('digest')
        if (record.get('name') != name or record.get('candidate_sha') != candidate
                or type(record.get('run_id')) is not str or record['run_id'] != run_id
                or record.get('source_sha') != source
                or type(record.get('built')) is not bool or record['built'] != (name in builds)
                or not isinstance(digest, str) or not DIGEST.fullmatch(digest)):
            raise ValueError(f'{name}: invalid or mismatched image record')
        if name not in builds and digest != reuse[name]:
            raise ValueError(f'{name}: digest does not match planned reuse')
        digests[name] = digest
    with open(output, 'a', encoding='utf-8', newline='\n') as stream:
        stream.write(''.join(f'{name}={digests[name]}\n' for name in IMAGES))
    print('Validated evidence for five release images')


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check-gates')
    collect_parser = sub.add_parser('collect')
    collect_parser.add_argument('image_artifacts_dir')
    args = parser.parse_args()
    try:
        plan = plan_from_env()
        if args.command == 'check-gates':
            check_gates(plan)
        else:
            collect(plan, args.image_artifacts_dir)
    except (ValueError, KeyError, OSError) as exc:
        print(f'Release evidence rejected: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
