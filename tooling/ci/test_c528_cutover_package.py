import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('c528', ROOT / 'infra/gitops/operations/prepare_c528_cutover.py')
c528 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c528)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = {'source_sha': 'a' * 40, **{k: 'sha256:' + str(i) * 64 for i, k in enumerate(c528.DIGEST_KEYS, 1)}}
        self.path = self.root / 'release.json'
        self.path.write_text(json.dumps(self.release), encoding='utf-8')
        self.args = (self.path, 'a' * 40, 'b' * 40, '69a2f746-52ef-4965-a581-4b9d3f6dba1e',
                     'infra/gitops/preview', 'Release', 'knowi_migrator', 'knowi_app', self.root / 'out')

    def prepare(self, *args, pinned=None):
        result = subprocess.CompletedProcess([], 0, stdout=json.dumps(self.release if pinned is None else pinned).encode())
        with patch.object(c528.subprocess, 'run', return_value=result):
            return c528.prepare(*(args or self.args))

    def test_package_has_guarded_patches_and_scoped_job(self):
        artifacts = self.prepare()
        pause = artifacts['01-pause-application.patch.json']
        switch = artifacts['04-switch-pinned-path.patch.json']
        for p in (pause, switch):
            self.assertIn({'op': 'test', 'path': '/metadata/uid', 'value': self.args[3]}, p)
            self.assertIn({'op': 'test', 'path': '/spec/source/targetRevision', 'value': 'Release'}, p)
        self.assertIn({'op': 'test', 'path': '/spec/syncPolicy/automated/enabled', 'value': False}, switch)
        self.assertIn({'op': 'replace', 'path': '/spec/source/targetRevision', 'value': 'b' * 40}, switch)
        job = artifacts['03-migration-job.json']['spec']
        self.assertEqual((job['backoffLimit'], job['activeDeadlineSeconds']), (0, 300))
        pod = job['template']
        container, = pod['spec']['containers']
        self.assertEqual(container['command'][-2:], ['upgrade', 'c528_course_leaders'])
        self.assertTrue(container['image'].endswith('@' + self.release['migration_digest']))
        env = {x['name']: x for x in container['env']}
        for name, value in [('KNOWI_MIGRATION_USER', 'knowi_c528_' + 'a' * 12), ('KNOWI_MIGRATION_OWNER', 'knowi_migrator'), ('KNOWI_MIGRATION_APP_ROLE', 'knowi_app'), ('KNOWI_MIGRATION_HOST', c528.HOST)]:
            self.assertEqual(env[name]['value'], value)
        policy = artifacts['02-migrator-networkpolicy.json']
        peer, = policy['spec']['ingress'][0]['from']
        self.assertEqual(peer['podSelector']['matchLabels'], pod['metadata']['labels'])
        self.assertEqual(policy['spec']['ingress'][0]['ports'], [{'protocol': 'TCP', 'port': 57152}])
        self.assertEqual(peer['namespaceSelector']['matchLabels'], {'kubernetes.io/metadata.name': 'pymentor'})
        self.assertEqual(policy['metadata']['namespace'], 'pymentor-rehearsal-data-20261109')
        with self.assertRaises(ValueError):
            self.prepare()

    def test_bad_manifest_or_wrong_commit_rejected_before_output(self):
        for key in c528.DIGEST_KEYS:
            with self.subTest(key=key):
                release = dict(self.release)
                release.pop(key)
                self.path.write_text(json.dumps(release), encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse(self.args[-1].exists())
        self.path.write_text(json.dumps(self.release), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.prepare(pinned={**self.release, 'analytics_digest': 'sha256:' + 'f' * 64})
        self.assertFalse(self.args[-1].exists())
        with patch.object(c528.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'git')):
            with self.assertRaises(ValueError):
                c528.prepare(*self.args)
        self.assertFalse(self.args[-1].exists())

    def test_invalid_identity_and_roles_fail(self):
        for index, value in [(1, 'Release'), (2, 'Release'), (3, 'wrong'), (6, 'knowi_app'), (7, 'knowi_c528_' + 'a' * 12)]:
            with self.subTest(index=index):
                args = list(self.args)
                args[index] = value
                with self.assertRaises(ValueError):
                    self.prepare(*args)
                self.assertFalse(self.args[-1].exists())


if __name__ == '__main__':
    unittest.main()
