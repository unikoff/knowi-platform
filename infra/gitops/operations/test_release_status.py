import importlib.util
import datetime
import json
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('release_status', Path(__file__).with_name('release_status.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
D = 'sha256:' + 'a' * 64
R = 'b' * 40


class ReleaseStatusTest(unittest.TestCase):
    def test_rollout_rejects_stale_or_wrong_digest(self):
        obj = {'metadata': {'generation': 2}, 'spec': {'replicas': 1, 'template': {'spec': {'containers': [{'name': 'core', 'image': 'repo@' + D}]}}}, 'status': {'phase': 'Healthy', 'observedGeneration': 2, 'stableRS': 'hash', 'currentPodHash': 'hash', 'readyReplicas': 1, 'updatedReplicas': 1, 'replicas': 1}}
        m.validate_rollout(obj, D, 'core')
        obj['status']['currentPodHash'] = 'old'
        with self.assertRaises(m.CheckError):
            m.validate_rollout(obj, D, 'core')
        obj['status']['currentPodHash'] = 'hash'
        with self.assertRaises(m.CheckError):
            m.validate_rollout(obj, 'sha256:' + 'c' * 64, 'core')

    def test_endpoint_target_uid_hash_and_image(self):
        pod = {'metadata': {'name': 'p', 'uid': 'u', 'labels': {'rollouts-pod-template-hash': 'h'}}, 'spec': {'containers': [{'name': 'core', 'image': 'repo@' + D}]}, 'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        slices = {'items': [{'metadata': {'labels': {'kubernetes.io/service-name': 'svc'}}, 'endpoints': [{'conditions': {'ready': True}, 'targetRef': {'kind': 'Pod', 'name': 'p', 'uid': 'u'}}]}]}
        self.assertEqual(m.endpoint_pods(slices, {'items': [pod]}, 'svc', D, 'core', 'h'), 1)
        slices['items'][0]['endpoints'][0]['targetRef']['uid'] = 'other'
        with self.assertRaises(m.CheckError):
            m.endpoint_pods(slices, {'items': [pod]}, 'svc', D, 'core', 'h')

    def test_traffic(self):
        data = {'spec': {'weighted': {'services': [{'name': 'svc', 'weight': 100}, {'name': 'svc-canary', 'weight': 0}]}}}
        m.weighted(data, 'svc', 'svc-canary')
        data['spec']['weighted']['services'][1]['weight'] = 1
        with self.assertRaises(m.CheckError):
            m.weighted(data, 'svc', 'svc-canary')

    def test_public_smoke_and_bad_revision(self):
        def request(base, path):
            if path == '/':
                return 200, {'Content-Type': 'text/html'}, b'<html><script src="/assets/app.js"></script></html>'
            if path == '/assets/app.js':
                return 200, {'Content-Type': 'application/javascript'}, b'js'
            if path == '/release-revision.txt':
                return 200, {}, (R + '\n').encode()
            return (401 if path in ('/api/user/me', '/api/user/booking-mode') else 200), {'X-Pymentor-Revision': R}, b'{"courses": []}'
        with patch.object(m, 'http_get', side_effect=request):
            m.smoke('https://example.invalid', R, R)
            with self.assertRaises(m.CheckError):
                m.smoke('https://example.invalid', 'c' * 40)
            with self.assertRaisesRegex(m.CheckError, 'frontend revision mismatch'):
                m.smoke('https://example.invalid', R, 'c' * 40)

    def test_backup_requires_fresh_restore_and_offsite_proof(self):
        identifier = 'knowi-core-20261120T120000Z-abcdef01'
        path = Path(identifier + '.json')
        item = {'id': identifier, 'created_at': '2026-11-20T12:00:00+00:00', 'server_restore_verified': True, 'offsite_verified': True}
        now = datetime.datetime(2026, 11, 20, 13, tzinfo=datetime.timezone.utc)
        with patch.object(Path, 'is_dir', return_value=False), patch.object(Path, 'read_text', return_value=json.dumps(item)):
            self.assertEqual(m.backup_evidence(path, 2, now)[0], identifier)
            with self.assertRaises(m.CheckError):
                m.backup_evidence(path, 0.5, now)
        item['offsite_verified'] = False
        with patch.object(Path, 'is_dir', return_value=False), patch.object(Path, 'read_text', return_value=json.dumps(item)):
            with self.assertRaises(m.CheckError):
                m.backup_evidence(path, 2, now)

    def test_public_only_does_not_invoke_kubectl_and_uses_core_source(self):
        sources = dict.fromkeys(('core', 'frontend', 'runner-pool', 'core-migrations', 'analytics'), R)
        sources['frontend'] = 'd' * 40
        data = json.dumps({'source_sha': 'c' * 40, 'image_sources': sources, 'core_digest': D, 'frontend_digest': D, 'runner_digest': D})
        with patch.object(Path, 'read_text', return_value=data), patch.object(m, 'smoke') as smoke, patch.object(m.subprocess, 'run') as run:
            self.assertEqual(m.main(['--public-only', '--release-file', 'synthetic.json', '--base-url', 'https://example.invalid']), 0)
            smoke.assert_called_once_with('https://example.invalid', R, 'd' * 40)
            run.assert_not_called()

    def test_schema_checks_target_and_revision_in_read_only_transaction(self):
        with patch.object(m.subprocess, 'run') as run:
            run.return_value.stdout = 'knowi_core|c528_course_leaders\n'
            m.verify_schema('knowi-readonly', 'c528_course_leaders')
            cmd = run.call_args.args[0]
            self.assertIn('service=knowi-readonly', cmd)
            self.assertIn('BEGIN READ ONLY', cmd[-1])
            run.return_value.stdout = 'test_db|c528_course_leaders\n'
            with self.assertRaises(m.CheckError):
                m.verify_schema('knowi-readonly', 'c528_course_leaders')


if __name__ == '__main__':
    unittest.main()
