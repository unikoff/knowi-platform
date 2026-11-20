"""Contracts for the release check shards and their strict JUnit gate."""
import json
import io
from contextlib import nullcontext
import unittest
from unittest.mock import patch

from tooling.ci import preview_checks


class PreviewShardsTests(unittest.TestCase):
    def test_unit_group_keeps_database_backed_analytics_out(self):
        with (patch.object(preview_checks, 'updater_tests'), patch.object(preview_checks, 'run') as run,
              patch.object(preview_checks, 'pytest_checked') as suite,
              patch.object(preview_checks.tempfile, 'TemporaryDirectory', return_value=nullcontext(preview_checks.ROOT))):
            preview_checks.main('unit')
        self.assertEqual([call.args[2].name for call in suite.call_args_list], ['unit.xml', 'backup-guards.xml'])
        self.assertFalse(any('tooling/ci/prepare_test_databases.py' in call.args[0] for call in run.call_args_list))

    def test_http_shards_cover_manifest_once_and_remain_ordered(self):
        manifest = json.loads((preview_checks.ROOT / 'services/core-api/tests/runtime_suites.json').read_text(encoding='utf-8'))
        files = manifest['http']
        first = preview_checks.http_partition(files, 'http-1')
        second = preview_checks.http_partition(files, 'http-2')
        self.assertTrue(first and second)
        self.assertEqual(len(files), len(set(files)), 'HTTP manifest contains duplicate entries')
        self.assertFalse(set(first) & set(second))
        self.assertEqual(set(first) | set(second), set(files))
        self.assertEqual(first, files[::2])
        self.assertEqual(second, files[1::2])

    def test_junit_rejects_skips_and_empty_results(self):
        for attributes in ('tests="1" skipped="1"', 'tests="0" skipped="0"',
                           'tests="1" failures="1"', 'tests="1" errors="1"'):
            with self.subTest(attributes=attributes), self.assertRaises(RuntimeError):
                preview_checks.require_test_results(io.StringIO(f'<testsuite {attributes}/>'))
        preview_checks.require_test_results(io.StringIO('<testsuite tests="1" skipped="0"/>'))


if __name__ == '__main__':
    unittest.main()
