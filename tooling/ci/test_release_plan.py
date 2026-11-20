import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time
import shutil
import stat
import uuid
import unittest

spec = importlib.util.spec_from_file_location('release_plan', Path(__file__).with_name('release_plan.py'))
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)


class ReleasePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / ('.release-plan-test-' + uuid.uuid4().hex)
        self.tmp.mkdir()
        self.old_cwd = os.getcwd()
        os.chdir(self.tmp)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.write('initial.txt', 'initial')
        self.commit('initial')
        self.source = self.sha()
        self.digests = {field: 'sha256:' + str(i) * 64 for i, field in enumerate(rp.DIGEST_FIELDS.values(), 1)}
        self.write(rp.RELEASE, json.dumps(dict(source_sha=self.source, **self.digests)))
        self.git('add', '.')
        self.git('-c', 'user.name=github-actions[bot]', '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
                 'commit', '-qm', 'chore(preview): update image digests [skip ci]')
        self.published = self.sha()

    def tearDown(self):
        os.chdir(self.old_cwd)
        def writable_retry(function, path, _error):
            os.chmod(path, stat.S_IWRITE)
            for attempt in range(20):
                try:
                    function(path)
                    return
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.05)
        shutil.rmtree(self.tmp, onexc=writable_retry)

    def git(self, *args):
        return subprocess.check_output(['git', *args], stderr=subprocess.PIPE).decode().strip()

    def write(self, path, text):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '-qm', message)

    def sha(self):
        return self.git('rev-parse', 'HEAD')

    def test_frontend_lesson_rebuilds_core_migrations_and_frontend_only(self):
        self.write('courses/backend/frontend/course/Lesson.tsx', 'lesson')
        self.commit('lesson')
        result = rp.plan('push', self.sha(), self.published)
        self.assertEqual(result['build'], ['core', 'core-migrations', 'frontend'])
        self.assertFalse(result['analytics_checks'])
        self.assertFalse(result['core_rehearsal'])
        self.assertEqual(set(result['reuse']), {'runner-pool', 'analytics'})
        self.assertEqual(result['image_sources']['analytics'], self.source)

    def test_failed_push_changes_are_included_even_when_event_before_is_later(self):
        self.write('services/analytics/pymentor_analytics/app.py', 'a')
        self.commit('failed release')
        failed = self.sha()
        self.write('apps/web/src/index.ts', 'b')
        self.commit('new release')
        result = rp.plan('push', self.sha(), failed)
        self.assertEqual(result['build'], ['frontend', 'analytics'])
        self.assertTrue(result['analytics_checks'])
        self.assertTrue(result['analytics_rehearsal'])

    def test_shared_course_backend_exercises_analytics_contracts(self):
        self.write('courses/shared/backend/catalog.py', 'shared')
        self.commit('shared')
        result = rp.plan('push', self.sha(), self.published)
        self.assertEqual(result['build'], ['core', 'core-migrations', 'frontend'])
        self.assertTrue(result['analytics_checks'])
        self.assertFalse(result['core_rehearsal'])

    def test_database_change_gates_rehearsals_without_rebuilding_unrelated_images(self):
        self.write('services/core-api/migrations/versions/new.py', 'new schema')
        self.commit('migration')
        result = rp.plan('push', self.sha(), self.published)
        self.assertEqual(result['build'], ['core-migrations'])
        self.assertTrue(result['core_rehearsal'])
        self.assertTrue(result['analytics_rehearsal'])

    def test_web_analytics_client_keeps_contract_checks(self):
        self.write('apps/web/src/features/analytics-tracker/client.ts', 'client')
        self.commit('browser analytics')
        result = rp.plan('push', self.sha(), self.published)
        self.assertEqual(result['build'], ['frontend'])
        self.assertTrue(result['analytics_checks'])
        self.assertFalse(result['core_rehearsal'])

    def test_web_styles_only_reuse_other_images_and_skip_analytics(self):
        self.write('apps/web/src/lesson.css', 'body { color: inherit }')
        self.commit('styles')
        result = rp.plan('push', self.sha(), self.published)
        self.assertEqual(result['build'], ['frontend'])
        self.assertFalse(result['analytics_checks'])
        self.assertFalse(result['core_rehearsal'])
        self.assertFalse(result['core_checks'])
        self.assertTrue(result['web_checks'])

    def test_gate_scope_scenarios(self):
        scenarios = {
            'apps/web/src/example.css': (False, True),
            'apps/web/public/logo.svg': (False, True),
            'apps/web/src/features/example.ts': (True, True),
            'services/analytics/pymentor_analytics/app.py': (False, False),
            'services/analytics/tests/cross_service/test_contract.py': (False, False),
            'services/core-api/pymentor_core/analytics_delivery/client.py': (True, True),
            'services/core-api/contracts/events.json': (True, True),
            'services/core-api/migrations/versions/new.py': (True, False),
            'services/core-api/tests/test_api.py': (True, False),
            'services/runner-pool/tests/test_runner.py': (True, False),
            'courses/backend/frontend/course/Lesson.tsx': (True, True),
            'courses/shared/backend/catalog.py': (True, True),
            'docs/example.md': (False, False),
            'unknown/file.txt': (True, True),
        }
        for path, expected in scenarios.items():
            with self.subTest(path=path):
                self.assertEqual(rp.check_scope(path), expected)

    def test_analytics_only_and_combined_scope_are_accumulated(self):
        self.write('services/analytics/pymentor_analytics/app.py', 'analytics')
        self.commit('analytics')
        result = rp.plan('pr', self.sha(), base_ref=self.published)
        self.assertTrue(result['analytics_checks'])
        self.assertFalse(result['core_checks'])
        self.assertFalse(result['web_checks'])
        self.write('services/runner-pool/tests/test_runner.py', 'runner')
        self.write('apps/web/src/example.css', 'style')
        self.commit('runner and style')
        result = rp.plan('pr', self.sha(), base_ref=self.published)
        self.assertTrue(result['core_checks'])
        self.assertTrue(result['web_checks'])
        self.assertTrue(result['analytics_checks'])

    def test_manual_or_incomplete_manifest_falls_back(self):
        self.write(rp.RELEASE, json.dumps(dict(source_sha=self.source, **self.digests)))
        self.write(rp.RELEASE, Path(rp.RELEASE).read_text() + '\n')
        self.commit('manual')
        self.assertEqual(rp.plan('push', self.sha(), self.sha())['build'], list(rp.IMAGES))
        self.assertEqual(rp.plan('push', self.sha(), 'missing')['build'], list(rp.IMAGES))
        del self.digests['analytics_digest']
        self.write(rp.RELEASE, json.dumps(dict(source_sha=self.sha(), **self.digests)))
        self.commit('incomplete')
        self.assertEqual(rp.plan('push', self.sha(), self.sha())['build'], list(rp.IMAGES))

    def test_pr_never_reuses_and_docs_do_not_deploy(self):
        self.write('docs/example.md', 'words')
        self.commit('docs')
        result = rp.plan('pr', self.sha(), base_ref=self.published)
        self.assertEqual(result['build'], [])
        self.assertEqual(result['reuse'], {})
        self.assertFalse(result['deploy'])
        self.assertFalse(result['core_checks'])
        self.assertFalse(result['web_checks'])
        self.assertTrue(all(sha == self.sha() for sha in result['image_sources'].values()))
        pushed = rp.plan('push', self.sha(), self.published)
        self.assertFalse(pushed['deploy'])
        self.assertEqual(set(pushed['reuse']), set(rp.IMAGES))

    def test_gitops_change_after_publication_deploys_even_if_before_event(self):
        self.write('infra/gitops/preview/applications.json', '{}')
        self.commit('manual manifest change')
        previous = self.sha()
        self.write('docs/example.md', 'words')
        self.commit('docs')
        result = rp.plan('push', self.sha(), previous)
        self.assertTrue(result['deploy'])
        self.assertEqual(result['build'], list(rp.IMAGES))

    def test_removing_generated_release_file_cannot_be_treated_as_no_change(self):
        Path(rp.RELEASE).unlink()
        self.commit('remove release record')
        result = rp.plan('push', self.sha(), self.published)
        self.assertTrue(result['deploy'])
        self.assertEqual(result['build'], list(rp.IMAGES))

    def test_unknown_and_full_override_are_conservative(self):
        self.write('unmapped.txt', 'unknown')
        self.commit('unknown')
        for result in (rp.plan('push', self.sha(), self.published),
                       rp.plan('push', self.sha(), self.published, force=True)):
            self.assertEqual(result['build'], list(rp.IMAGES))
            self.assertTrue(result['analytics_checks'])
            self.assertTrue(result['core_rehearsal'])
            self.assertTrue(result['core_checks'])
            self.assertTrue(result['web_checks'])


if __name__ == '__main__':
    unittest.main()
