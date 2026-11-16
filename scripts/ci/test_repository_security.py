import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from repository_security import apply, plan, verify_ruleset


class RepositorySecurity(unittest.TestCase):
    def test_private_repository_is_never_made_public_or_mutated(self):
        calls = []
        def api(path, method='GET', *args, **kwargs):
            calls.append((method, path))
            return {'private': True, 'visibility': 'private', 'permissions': {'admin': True}}
        with tempfile.TemporaryDirectory() as directory, patch('repository_security.api', side_effect=api):
            record = Path(directory) / 'previous.json'
            with self.assertRaisesRegex(RuntimeError, 'never changes visibility'):
                apply(record)
            self.assertFalse(record.exists())
        self.assertEqual(calls, [('GET', 'repos/zapatacomputing/forshor-submissions')])

    def test_history_rules_allow_ordinary_pushes_without_bypasses(self):
        self.assertEqual(len(plan()['rulesets']), 1)
        history = plan()['rulesets'][0]
        self.assertEqual(history['bypass_actors'], [])
        self.assertEqual({r['type'] for r in history['rules']}, {'deletion', 'non_fast_forward'})
        self.assertEqual(history['conditions']['ref_name']['include'], ['refs/heads/main', 'refs/heads/submission-results'])
        for actor in [{'actor_type': 'OrganizationAdmin', 'bypass_mode': 'always'},
                      {'actor_type': 'Integration', 'actor_id': 15368, 'bypass_mode': 'always'}]:
            weakened = copy.deepcopy(history)
            weakened['bypass_actors'].append(actor)
            with self.assertRaises(RuntimeError):
                verify_ruleset(weakened, history)

    def test_disabled_or_wrong_branch_rules_are_refused(self):
        expected = plan()['rulesets'][0]
        verify_ruleset({**expected, 'id': 123}, expected)
        for key, value in [('enforcement', 'disabled'), ('conditions', {'ref_name': {'include': ['refs/heads/other'], 'exclude': []}})]:
            actual = copy.deepcopy(expected)
            actual[key] = value
            with self.assertRaises(RuntimeError):
                verify_ruleset(actual, expected)

    def test_apply_only_changes_history_rules_and_preserves_audit(self):
        mutations = []
        saved = {}
        def api(path, method='GET', body=None, **kwargs):
            if method != 'GET':
                mutations.append((method, path, body))
                saved[1] = {**body, 'id': 1}
                return saved[1]
            if path == 'repos/zapatacomputing/forshor-submissions':
                return {'private': False, 'visibility': 'public', 'permissions': {'admin': True}}
            if '/rulesets?' in path:
                return []
            if path.endswith('/rulesets/1'):
                return saved[1]
            return {'default_workflow_permissions': 'read', 'can_approve_pull_request_reviews': False}
        with tempfile.TemporaryDirectory() as directory, patch('repository_security.api', side_effect=api):
            record = Path(directory) / 'previous.json'
            apply(record)
            self.assertTrue(record.is_file())
        self.assertEqual(mutations, [('POST', 'repos/zapatacomputing/forshor-submissions/rulesets', plan()['rulesets'][0])])


if __name__ == '__main__':
    unittest.main()
