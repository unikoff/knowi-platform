import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name('release_evidence.py')
IMAGES = ('core', 'core-migrations', 'frontend', 'runner-pool', 'analytics')
SHA = 'a' * 40
OLD_SHA = 'b' * 40
DIGEST = 'sha256:' + 'c' * 64


class ReleaseEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.output = self.directory / 'output'
        self.output.write_text('existing=value\n', encoding='utf-8')
        self.plan = {
            'core_checks': False, 'core_rehearsal': True, 'web_checks': False,
            'analytics_checks': True, 'analytics_rehearsal': False,
            'build': ['core'],
            'reuse': {name: DIGEST for name in IMAGES if name != 'core'},
            'image_sources': {name: SHA if name == 'core' else OLD_SHA for name in IMAGES},
        }
        self.env = dict(os.environ, GITHUB_SHA=SHA, GITHUB_RUN_ID='123', GITHUB_OUTPUT=str(self.output),
                        CORE_RESULT='success', WEB_RESULT='skipped', ANALYTICS_RESULT='success',
                        ANALYTICS_REHEARSAL_RESULT='skipped')
        for name in IMAGES:
            path = self.record_path(name)
            path.parent.mkdir()
            path.write_text(json.dumps(dict(name=name, candidate_sha=SHA, run_id='123', digest=DIGEST,
                                            source_sha=self.plan['image_sources'][name], built=name == 'core')),
                            encoding='utf-8')

    def record_path(self, name):
        return self.directory / f'release-image-{name}' / 'image.json'

    def run_cli(self, *args):
        env = dict(self.env, PLAN=json.dumps(self.plan))
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], env=env,
                              capture_output=True, text=True)

    def change_record(self, image, **changes):
        path = self.record_path(image)
        record = json.loads(path.read_text(encoding='utf-8'))
        record.update(changes)
        path.write_text(json.dumps(record), encoding='utf-8')

    def test_selected_and_unselected_gates(self):
        self.assertEqual(self.run_cli('check-gates').returncode, 0)
        for variable, value in [('CORE_RESULT', 'skipped'), ('ANALYTICS_RESULT', 'failure'),
                                ('WEB_RESULT', 'success'), ('WEB_RESULT', 'cancelled')]:
            with self.subTest(variable=variable, value=value):
                previous = self.env[variable]
                self.env[variable] = value
                self.assertNotEqual(self.run_cli('check-gates').returncode, 0)
                self.env[variable] = previous
        self.plan['core_checks'] = 'false'
        self.assertNotEqual(self.run_cli('check-gates').returncode, 0)

    def test_collect_valid_evidence(self):
        result = self.run_cli('collect', self.directory)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text(encoding='utf-8').splitlines(),
                         ['existing=value', *(f'{name}={DIGEST}' for name in IMAGES)])

    def test_bad_or_missing_evidence_never_writes_partial_output(self):
        cases = [dict(digest='sha256:bad'), dict(digest='sha256:' + 'd' * 64),
                 dict(candidate_sha=OLD_SHA), dict(run_id='122'), dict(run_id=123),
                 dict(source_sha=SHA), dict(built=True), dict(name='core')]
        original = self.record_path('analytics').read_text(encoding='utf-8')
        for changes in cases:
            with self.subTest(changes=changes):
                self.change_record('analytics', **changes)
                result = self.run_cli('collect', self.directory)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.output.read_text(encoding='utf-8'), 'existing=value\n')
                self.record_path('analytics').write_text(original, encoding='utf-8')
        self.record_path('analytics').unlink()
        self.assertNotEqual(self.run_cli('collect', self.directory).returncode, 0)
        self.assertEqual(self.output.read_text(encoding='utf-8'), 'existing=value\n')


if __name__ == '__main__':
    unittest.main()
