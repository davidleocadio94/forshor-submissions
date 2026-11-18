"""Contributor-side trust, freshness, and publication correlation regressions."""
import argparse
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('contributor_cli', str(Path(__file__).parents[1] / 'forshor'))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


class WorkingFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        git(self.root, 'init', '-b', 'main')
        git(self.root, 'config', 'user.name', 'CLI tests')
        git(self.root, 'config', 'user.email', 'cli@invalid')
        (self.root / cli.POLICY).parent.mkdir(parents=True)
        (self.root / cli.POLICY).write_text('original policy\n')
        (self.root / 'protected.lean').write_text('trusted statement\n')
        (self.root / '.gitignore').write_text('artifacts/\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-m', 'baseline')
        self.base = git(self.root, 'rev-parse', 'HEAD')

    def test_check_includes_uncommitted_and_untracked_lean_helpers(self):
        before = cli.fingerprint(self.root, self.base)
        (self.root / cli.POLICY).write_text('edited policy\n')
        helper = self.root / cli.HELPERS / 'Nested/Proof.lean'
        helper.parent.mkdir(parents=True)
        helper.write_text('new proof\n')
        paths = cli.validate_changes(self.root, self.base)
        self.assertEqual(paths, [cli.POLICY, str(helper.relative_to(self.root))])
        self.assertNotEqual(before, cli.fingerprint(self.root, self.base))
        self.assertEqual(git(self.root, 'rev-parse', 'HEAD'), self.base)

    def test_protected_rename_cannot_hide_its_deleted_source(self):
        target = self.root / cli.HELPERS / 'Stolen.lean'
        target.parent.mkdir(parents=True)
        git(self.root, 'mv', 'protected.lean', str(target.relative_to(self.root)))
        git(self.root, 'commit', '-m', 'rename protected file')
        with self.assertRaisesRegex(cli.UserError, 'protected.lean'):
            cli.validate_changes(self.root, self.base)

    def test_staged_unrelated_files_block_submission_before_any_push(self):
        (self.root / 'protected.lean').write_text('changed statement\n')
        git(self.root, 'add', 'protected.lean')
        args = argparse.Namespace(watch=False, json=False, timeout=1, title='test')
        with patch.object(cli, 'authenticate'), patch.object(cli, 'repository', return_value=self.root), \
                patch.object(cli, 'refresh_base', return_value=self.base), patch.object(cli, 'network_git') as push:
            with self.assertRaisesRegex(cli.UserError, 'protected.lean'):
                cli.submit(args)
            push.assert_not_called()

    def test_nested_symlink_is_refused(self):
        helper = self.root / cli.HELPERS / 'Escape.lean'
        helper.parent.mkdir(parents=True)
        helper.symlink_to(self.root / 'protected.lean')
        with self.assertRaisesRegex(cli.UserError, 'symlink'):
            cli.validate_changes(self.root, self.base)

    def test_a_policy_file_is_the_whole_submission_and_helpers_are_refused(self):
        self.assertFalse(cli.policy_format(self.root), 'the older stub is not a policy file')
        (self.root / cli.POLICY).write_text('import ForShor.X\nnamespace PolicySubmission\ndef policy := 1\nend PolicySubmission\n')
        self.assertTrue(cli.policy_format(self.root))
        self.assertEqual(cli.validate_changes(self.root, self.base), [cli.POLICY])
        self.assertEqual(cli.source_files(self.root), [self.root / cli.POLICY])
        helper = self.root / cli.HELPERS / 'Proof.lean'
        helper.parent.mkdir(parents=True)
        helper.write_text('a helper\n')
        with self.assertRaisesRegex(cli.UserError, 'Only .*Policy.lean and the optional .*summary.tex may change in a policy submission'):
            cli.validate_changes(self.root, self.base)
        self.assertEqual(cli.source_files(self.root), [self.root / cli.POLICY], 'a helper is never copied into the checker')
        helper.unlink()
        (self.root / 'protected.lean').write_text('changed\n')
        with self.assertRaisesRegex(cli.UserError, 'protected.lean'):
            cli.validate_changes(self.root, self.base)

    # ---- the optional summary.tex
    HOSTILE = '\\documentclass{article}\n\\immediate\\write18{touch CANARY}\n\\input{/etc/passwd}\n@everyone <script>alert(1)</script>\n' + 'x' * 60000 + '\r\n\tend\n'

    def policy(self):
        (self.root / cli.POLICY).write_text('import ForShor.X\nnamespace PolicySubmission\ndef policy := 1\nend PolicySubmission\n')

    def test_summary_path_is_the_agreed_one(self):
        self.assertEqual(cli.SUMMARY, 'TableGeneration/Submission/summary.tex')

    def test_a_summary_with_a_policy_is_part_of_the_submission_and_of_its_fingerprint(self):
        self.policy()
        before = cli.fingerprint(self.root, self.base)
        summary = self.root / cli.SUMMARY
        summary.write_bytes(self.HOSTILE.encode())
        self.assertEqual(cli.validate_changes(self.root, self.base), [cli.POLICY, cli.SUMMARY])
        self.assertEqual(cli.source_files(self.root), [self.root / cli.POLICY, summary])
        after = cli.fingerprint(self.root, self.base)
        self.assertNotEqual(before, after)
        summary.write_bytes(self.HOSTILE.encode() + b'x')
        self.assertNotEqual(after, cli.fingerprint(self.root, self.base), 'editing the summary invalidates a passed check')

    def test_a_policy_alone_is_unchanged(self):
        self.policy()
        self.assertEqual(cli.validate_changes(self.root, self.base), [cli.POLICY])
        self.assertEqual(cli.source_files(self.root), [self.root / cli.POLICY])

    def test_a_summary_alone_is_not_a_submission(self):
        (self.root / cli.SUMMARY).write_text('only a summary\n')
        with self.assertRaisesRegex(cli.UserError, 'cannot be submitted alone'):
            cli.validate_changes(self.root, self.base)
        self.policy()
        git(self.root, 'add', cli.POLICY)
        git(self.root, 'commit', '-m', 'a policy at the base')
        base = git(self.root, 'rev-parse', 'HEAD')
        (self.root / cli.SUMMARY).write_text('still only a summary\n')
        with self.assertRaisesRegex(cli.UserError, 'cannot be submitted alone'):
            cli.validate_changes(self.root, base)

    def test_nothing_else_may_travel_with_the_pair(self):
        self.policy()
        (self.root / cli.SUMMARY).write_text('fine\n')
        for extra in ('TableGeneration/Submission/notes.tex', cli.HELPERS + 'Helper.lean', cli.HELPERS + 'summary.tex', 'README.md'):
            target = self.root / extra
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('x\n')
            with self.assertRaisesRegex(cli.UserError, 'Only .*Policy.lean and the optional .*summary.tex may change'):
                cli.validate_changes(self.root, self.base)
            target.unlink()

    def test_every_summary_rule_is_checked_before_anything_is_built(self):
        self.policy()
        summary = self.root / cli.SUMMARY
        cases = [('symbolic link', lambda: summary.symlink_to(self.root / 'protected.lean')),
                 ('not a directory', lambda: summary.mkdir()),
                 ('limit is 102,400', lambda: summary.write_bytes(b'a' * 102401)),
                 ('must be UTF-8', lambda: summary.write_bytes(b'caf\xe9\n')),
                 ('U\\+0000', lambda: summary.write_bytes(b'a\x00b\n')),
                 ('is empty', lambda: summary.write_bytes(b''))]
        for expected, make in cases:
            if summary.is_symlink() or summary.is_file():
                summary.unlink()
            elif summary.is_dir():
                summary.rmdir()
            make()
            with self.assertRaisesRegex(cli.UserError, expected):
                cli.validate_changes(self.root, self.base)

    def test_a_deleted_summary_is_refused(self):
        self.policy()
        (self.root / cli.SUMMARY).write_text('x\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-m', 'summary at the base')
        base = git(self.root, 'rev-parse', 'HEAD')
        (self.root / cli.SUMMARY).unlink()
        with self.assertRaisesRegex(cli.UserError, 'deleted or renamed'):
            cli.validate_changes(self.root, base)

    def test_submit_commits_the_summary_with_the_policy(self):
        self.policy()
        (self.root / cli.SUMMARY).write_bytes(self.HOSTILE.encode())
        git(self.root, 'switch', '-c', 'submission/with-summary')
        digest = cli.fingerprint(self.root, self.base)
        cli.write_state(self.root, {'checked': {'base': self.base, 'digest': digest}})
        args = argparse.Namespace(watch=False, json=False, timeout=1, title='test')
        calls = []
        def fake_run(command, cwd=None, env=None, capture=True, check=True):
            calls.append(command)
            if command[:3] == ['gh', 'pr', 'list']:
                return SimpleNamespace(stdout='[]', returncode=0)
            if command[:3] == ['gh', 'api', 'user']:
                return SimpleNamespace(stdout=json.dumps({'login': 'tester', 'id': 1}), returncode=0)
            if command[:3] == ['gh', 'pr', 'create']:
                return SimpleNamespace(stdout='https://github.com/zapatacomputing/forshor-submissions/pull/9\n', returncode=0)
            if command[0] == 'gh':
                self.fail(f'Unexpected GitHub command in an offline test: {command}')
            return cli_run(command, cwd=cwd, env=env, capture=capture, check=check)
        cli_run = cli.run
        with patch.object(cli, 'authenticate'), patch.object(cli, 'repository', return_value=self.root), patch.object(cli, 'refresh_base', return_value=self.base), \
                patch.object(cli, 'submission_repository', return_value=cli.REPO), patch.object(cli, 'pull_requests', return_value=[]), \
                patch.object(cli, 'run', side_effect=fake_run), patch.object(cli, 'network_git'), patch.object(cli, 'api', return_value={}) as fetch_pr, \
                patch.object(cli, 'validate_pr'), patch.object(cli, 'watch', return_value=0):
            cli.submit(args)
        fetch_pr.assert_called_once_with('pulls/9')
        self.assertEqual(sorted(git(self.root, 'show', '--name-only', '--format=', 'HEAD').splitlines()), [cli.POLICY, cli.SUMMARY])
        self.assertEqual(subprocess.check_output(['git', 'show', f'HEAD:{cli.SUMMARY}'], cwd=self.root), self.HOSTILE.encode(), 'committed byte for byte')

    def test_nothing_in_the_cli_runs_the_summary(self):
        """The CLI hands the file to the verifier's copy as bytes; it never starts a TeX engine or anything else for it."""
        source = (Path(__file__).parents[1] / 'forshor').read_text()
        for word in ('pdflatex', 'xelatex', 'lualatex', 'latexmk', 'tectonic', 'write18', 'shell-escape'):
            self.assertNotIn(word, source)

    def test_the_ptp_namespace_is_a_policy_file_too_and_a_symlink_is_not(self):
        (self.root / cli.POLICY).write_text('namespace PTPPolicySubmission\nend PTPPolicySubmission\n')
        self.assertTrue(cli.policy_format(self.root))
        (self.root / cli.POLICY).unlink()
        (self.root / cli.POLICY).symlink_to(self.root / 'protected.lean')
        self.assertFalse(cli.policy_format(self.root))

    def test_the_trusted_toolchain_may_be_a_pinned_release_or_release_candidate_and_nothing_else(self):
        for good in ('leanprover/lean4:v4.28.0', 'leanprover/lean4:v4.36.0-rc1'):
            self.assertTrue(cli.supported_toolchain(good), good)
        for bad in ('leanprover/lean4:stable', 'leanprover/lean4:nightly-2026-01-01', 'leanprover/lean4:v4.36.0-rc', 'leanprover/lean4:v4.36.0-rc1\nx',
                    'leanprover/lean4:v4.36', 'other/lean4:v4.36.0', 'leanprover/lean4:v4.36.0-beta1'):
            self.assertFalse(cli.supported_toolchain(bad), bad)

    def test_checker_cache_cannot_replace_an_unrecognized_directory(self):
        cache = cli.storage(self.root) / 'checker'
        cache.mkdir()
        sentinel = cache / 'keep.txt'
        sentinel.write_text('user data')
        with self.assertRaisesRegex(cli.UserError, 'unrecognized'):
            cli.trusted_checkout(self.root, self.base)
        self.assertEqual(sentinel.read_text(), 'user data')

    def test_full_check_rejects_a_preflight_only_success(self):
        artifacts = self.root / 'artifacts/forshor/verification'
        artifacts.mkdir(parents=True)
        (artifacts / 'table-generation-submission-result.json').write_text(json.dumps({'status': 'success', 'phase': 'preflight'}))
        # Pre-create the trusted clone and let check snapshot real edited files.
        cache = self.root / '.git/fake-checker'
        subprocess.run(['git', 'clone', '--quiet', str(self.root), str(cache)], check=True)
        (self.root / cli.POLICY).write_text('edited policy\n')
        with patch.object(cli, 'authenticate'), patch.object(cli, 'lean_environment', return_value={}), \
                patch.object(cli, 'prepare', return_value=cache), patch.object(cli, 'logged'):
            with self.assertRaisesRegex(cli.UserError, 'Full verification'):
                cli.check(self.root, self.base)
        self.assertNotIn('checked', cli.read_state(self.root))
        self.assertEqual((self.root / cli.POLICY).read_text(), 'edited policy\n')
        self.assertEqual(git(self.root, 'rev-parse', 'HEAD'), self.base)

    def test_submit_rechecks_after_an_edit_instead_of_reusing_stale_success(self):
        cli.write_state(self.root, {'checked': {'base': self.base, 'digest': cli.fingerprint(self.root, self.base)}})
        (self.root / cli.POLICY).write_text('changed after verification\n')
        args = argparse.Namespace(watch=False, json=False, timeout=1, title='test')
        with patch.object(cli, 'authenticate'), patch.object(cli, 'repository', return_value=self.root), \
                patch.object(cli, 'refresh_base', return_value=self.base), \
                patch.object(cli, 'check', side_effect=cli.UserError('check rejected edit')) as check, \
                patch.object(cli, 'network_git') as push:
            with self.assertRaisesRegex(cli.UserError, 'check rejected edit'):
                cli.submit(args)
            check.assert_called_once_with(self.root, self.base)
            push.assert_not_called()

    def test_local_policy_source_is_refused_before_lean_or_dependency_setup(self):
        self.policy()
        (self.root / cli.POLICY).write_text((self.root / cli.POLICY).read_text() + '\n#eval IO.println "canary"\n')
        trusted = Path(__file__).resolve().parents[1]
        with patch.object(cli, 'authenticate'), patch.object(cli, 'trusted_checkout', return_value=trusted) as checkout, \
                patch.object(cli, 'lean_environment') as setup:
            with self.assertRaisesRegex(cli.UserError, 'build-time commands'):
                cli.check(self.root, self.base)
            checkout.assert_called_once_with(self.root, self.base)
            setup.assert_not_called()

    def test_submit_reuses_existing_pr_after_interrupted_state_save(self):
        git(self.root, 'switch', '-c', 'submission/test')
        (self.root / cli.POLICY).write_text('new table\n')
        git(self.root, 'add', cli.POLICY)
        git(self.root, 'commit', '-m', 'table')
        head = git(self.root, 'rev-parse', 'HEAD')
        cli.write_state(self.root, {'checked': {'base': self.base, 'digest': cli.fingerprint(self.root, self.base)}})
        pr = {'number': 12, 'url': 'https://github.com/example/pull/12', 'headRefOid': head, 'state': 'OPEN'}
        details = {'head': {'sha': head, 'repo': {'full_name': cli.REPO}},
                   'base': {'ref': 'main', 'repo': {'full_name': cli.REPO}}}
        args = argparse.Namespace(watch=False, json=False, timeout=1, title='test')
        with patch.object(cli, 'authenticate'), patch.object(cli, 'repository', return_value=self.root), \
                patch.object(cli, 'refresh_base', return_value=self.base), \
                patch.object(cli, 'submission_repository', return_value=cli.REPO), \
                patch.object(cli, 'pull_requests', return_value=[pr]) as github, \
                patch.object(cli, 'api', return_value=details), patch.object(cli, 'network_git'), \
                patch.object(cli, 'watch'), patch.object(cli, 'check') as check:
            cli.submit(args)
            check.assert_not_called()
            self.assertEqual(github.call_count, 1, 'Only PR discovery is needed; no second PR creation')
            self.assertEqual(cli.read_state(self.root)['submission']['pr'], 12)

    def test_two_commands_cannot_reset_the_same_checker_concurrently(self):
        with cli.repository_lock(self.root):
            with self.assertRaisesRegex(cli.UserError, 'Another ForShor command'):
                with cli.repository_lock(self.root):
                    self.fail('A second command entered the checker')

    def closed_pr_iteration(self, edit):
        git(self.root, 'switch', '-c', 'submission/accepted')
        (self.root / cli.POLICY).write_text('accepted table\n')
        git(self.root, 'add', cli.POLICY)
        git(self.root, 'commit', '-m', 'accepted table')
        accepted_head = git(self.root, 'rev-parse', 'HEAD')
        if edit:
            (self.root / cli.POLICY).write_text('next table\n')
            if edit == 'committed':
                git(self.root, 'add', cli.POLICY)
                git(self.root, 'commit', '-m', 'next table')
        cli.write_state(self.root, {'checked': {'base': self.base, 'digest': cli.fingerprint(self.root, self.base)}})
        pr = {'number': 12, 'url': 'https://github.com/example/pull/12', 'headRefOid': accepted_head, 'state': 'CLOSED'}
        def details(_):
            return {'state': 'closed' if not edit else 'open',
                    'head': {'sha': git(self.root, 'rev-parse', 'HEAD'), 'repo': {'full_name': cli.REPO}},
                    'base': {'ref': 'main', 'repo': {'full_name': cli.REPO}}}
        original_run = cli.run
        creations = []
        def run(args, **kwargs):
            if args[:3] == ['gh', 'pr', 'create']:
                creations.append(args)
                return SimpleNamespace(stdout='https://github.com/example/pull/13\n')
            return original_run(args, **kwargs)
        def github(*args):
            return {'login': 'test', 'id': 1} if args == ('api', 'user') else [pr]
        args = argparse.Namespace(watch=False, json=False, timeout=1, title='test')
        with patch.object(cli, 'authenticate'), patch.object(cli, 'repository', return_value=self.root), \
                patch.object(cli, 'refresh_base', return_value=self.base), \
                patch.object(cli, 'submission_repository', return_value=cli.REPO), patch.object(cli, 'pull_requests', return_value=[pr]), \
                patch.object(cli, 'gh_json', side_effect=github), patch.object(cli, 'run', side_effect=run), \
                patch.object(cli, 'api', side_effect=details), patch.object(cli, 'network_git') as push, \
                patch.object(cli, 'watch'), patch.object(cli, 'check') as check:
            cli.submit(args)
            check.assert_not_called()
        submission = cli.read_state(self.root)['submission']
        self.assertEqual(submission['pr'], 13 if edit else 12)
        self.assertEqual(len(creations), 1 if edit else 0)
        if edit:
            self.assertNotEqual(submission['branch'], 'submission/accepted')
            self.assertNotIn('HEAD:refs/heads/submission/accepted', push.call_args.args)
        else:
            self.assertEqual(submission['branch'], 'submission/accepted')

    def test_new_working_table_after_closed_pr_creates_new_branch_and_pr(self):
        self.closed_pr_iteration('working')

    def test_new_committed_table_after_closed_pr_creates_new_branch_and_pr(self):
        self.closed_pr_iteration('committed')

    def test_unchanged_accepted_submission_reuses_closed_pr_for_status(self):
        self.closed_pr_iteration(None)

    def test_fork_submission_pushes_only_to_fork_and_binds_the_pr_to_it(self):
        # Real commits and a real Git push: the upstream bare repository must
        # remain unchanged even though the test participant can write locally.
        upstream = self.root.parent / (self.root.name + '-upstream.git')
        fork = self.root.parent / (self.root.name + '-fork.git')
        self.addCleanup(lambda: __import__('shutil').rmtree(upstream))
        self.addCleanup(lambda: __import__('shutil').rmtree(fork))
        subprocess.run(['git', 'clone', '--quiet', '--bare', str(self.root), str(upstream)], check=True)
        subprocess.run(['git', 'clone', '--quiet', '--bare', str(upstream), str(fork)], check=True)
        self.policy()
        (self.root / cli.SUMMARY).write_text('Explanation, retained as text.\n')
        cli.write_state(self.root, {'checked': {'base': self.base, 'digest': cli.fingerprint(self.root, self.base)}})
        source_repo = 'participant/forshor-submissions'
        calls = []
        def push(root, *args):
            self.assertEqual(args[:2], ('push', f'https://github.com/{source_repo}.git'))
            return git(root, 'push', str(fork), args[-1])
        original_run = cli.run
        def run(args, **kwargs):
            if args[:3] == ['gh', 'pr', 'create']:
                calls.append(args)
                return SimpleNamespace(stdout='https://github.com/zapatacomputing/forshor-submissions/pull/99\n')
            if args[:3] == ['gh', 'api', 'user']:
                return SimpleNamespace(stdout=json.dumps({'login': 'participant', 'id': 1}), returncode=0)
            if args[0] == 'gh':
                self.fail(f'Unexpected network command in fork test: {args}')
            return original_run(args, **kwargs)
        def pr(_):
            return {'head': {'sha': git(self.root, 'rev-parse', 'HEAD'), 'repo': {'full_name': source_repo}},
                    'base': {'ref': 'main', 'repo': {'full_name': cli.REPO}}}
        args = argparse.Namespace(watch=False, json=False, timeout=1, title='Test public submission')
        with patch.object(cli, 'authenticate'), patch.object(cli, 'repository', return_value=self.root), \
                patch.object(cli, 'refresh_base', return_value=self.base), \
                patch.object(cli, 'submission_repository', return_value=source_repo), \
                patch.object(cli, 'pull_requests', return_value=[]), patch.object(cli, 'api', side_effect=pr), \
                patch.object(cli, 'network_git', side_effect=push), patch.object(cli, 'watch'), \
                patch.object(cli, 'run', side_effect=run):
            cli.submit(args)
        state = cli.read_state(self.root)['submission']
        self.assertEqual(state['repository'], source_repo)
        self.assertEqual(git(fork, 'show', f'{state["branch"]}:{cli.POLICY}'), (self.root / cli.POLICY).read_text().strip())
        self.assertEqual(git(fork, 'show', f'{state["branch"]}:{cli.SUMMARY}'), 'Explanation, retained as text.')
        self.assertEqual(git(upstream, 'for-each-ref', '--format=%(refname)'), 'refs/heads/main')
        self.assertEqual(git(upstream, 'rev-parse', 'main'), self.base)
        self.assertEqual(calls[0][calls[0].index('--head') + 1], f'participant:{state["branch"]}')


class ForkRouting(unittest.TestCase):
    FORK = {'full_name': 'participant/forshor-submissions', 'fork': True,
            'parent': {'full_name': cli.REPO}, 'source': {'full_name': cli.REPO}}

    def test_public_maintainer_also_uses_fork_without_requesting_upstream_write(self):
        with patch.object(cli, 'github_repository', side_effect=[{'private': False, 'permissions': {'push': True}}, self.FORK]), \
                patch.object(cli, 'gh_json', return_value={'login': 'participant'}), patch.object(cli, 'run') as run:
            self.assertEqual(cli.submission_repository(Path('.')), self.FORK['full_name'])
            run.assert_not_called()

    def test_missing_fork_is_created_without_cloning_or_changing_remotes(self):
        with patch.object(cli, 'github_repository', side_effect=[{'private': False}, None, self.FORK]), \
                patch.object(cli, 'gh_json', return_value={'login': 'participant'}), patch.object(cli, 'run') as run:
            self.assertEqual(cli.submission_repository(Path('.')), self.FORK['full_name'])
            run.assert_called_once_with(['gh', 'repo', 'fork', cli.REPO, '--clone=false', '--remote=false'])

    def test_an_unrelated_existing_repository_cannot_be_used_or_overwritten(self):
        with patch.object(cli, 'github_repository', side_effect=[{'private': False}, {'fork': False, 'full_name': self.FORK['full_name']}]), \
                patch.object(cli, 'gh_json', return_value={'login': 'participant'}), patch.object(cli, 'run') as run:
            with self.assertRaisesRegex(cli.UserError, 'not your fork'):
                cli.submission_repository(Path('.'))
            run.assert_not_called()

    def test_private_staging_writer_retains_existing_route(self):
        with patch.object(cli, 'github_repository', return_value={'private': True, 'permissions': {'push': True}}), \
                patch.object(cli, 'gh_json') as github:
            self.assertEqual(cli.submission_repository(Path('.')), cli.REPO)
            github.assert_not_called()

    def test_a_private_read_only_participant_does_not_get_upstream_write(self):
        with patch.object(cli, 'github_repository', side_effect=[{'private': True, 'permissions': {'push': False}}, None]), \
                patch.object(cli, 'gh_json', return_value={'login': 'participant'}), \
                patch.object(cli, 'run', side_effect=cli.UserError('Private forks are disabled')):
            with self.assertRaisesRegex(cli.UserError, 'Private forks are disabled'):
                cli.submission_repository(Path('.'))

    def test_api_denial_is_not_treated_as_a_missing_fork(self):
        response = SimpleNamespace(returncode=1, stdout=json.dumps({'status': '403'}))
        with patch.object(cli, 'run', return_value=response):
            with self.assertRaisesRegex(cli.UserError, 'Could not inspect'):
                cli.github_repository(self.FORK['full_name'], missing_ok=True)
        response.stdout = json.dumps({'status': '404'})
        with patch.object(cli, 'run', return_value=response):
            self.assertIsNone(cli.github_repository(self.FORK['full_name'], missing_ok=True))

    def test_verifier_fetch_is_fixed_even_when_origin_is_a_fork(self):
        with patch.object(cli, 'network_git') as fetch, patch.object(cli, 'git', return_value='trusted-main'):
            self.assertEqual(cli.refresh_base(Path('.')), 'trusted-main')
        self.assertEqual(fetch.call_args.args[1:4], ('fetch', '--no-tags', f'https://github.com/{cli.REPO}.git'))
        self.assertIn('refs/remotes/forshor-upstream/main', fetch.call_args.args[-1])

    def test_repository_accepts_a_verified_github_fork_and_refuses_lookalikes(self):
        def check(origin, details):
            with patch.object(cli, 'run', return_value=SimpleNamespace(returncode=0, stdout='/tmp/checkout\n')), \
                    patch.object(cli, 'git', return_value=origin), patch.object(cli, 'github_repository', return_value=details):
                return cli.repository()
        self.assertEqual(check(f'https://github.com/{self.FORK["full_name"]}.git', self.FORK), Path('/tmp/checkout').resolve())
        for origin, details in [(f'https://github.com/{self.FORK["full_name"]}.git', {'fork': False}),
                                (f'https://github.com.evil/{cli.REPO}', self.FORK),
                                (f'https://token@github.com/{cli.REPO}', self.FORK)]:
            with self.assertRaises(cli.UserError):
                check(origin, details)

    def test_pr_discovery_filters_out_another_forks_identically_named_branch(self):
        def pr(name, number):
            return {'number': number, 'html_url': f'https://github.com/example/pull/{number}', 'state': 'open',
                    'head': {'sha': 'a' * 40, 'repo': {'full_name': name} if name else None}}
        with patch.object(cli, 'gh_json', return_value=[[pr('other/forshor-submissions', 1)], [pr(self.FORK['full_name'], 2), pr(None, 3)]]) as gh:
            records = cli.pull_requests('submission/test', self.FORK['full_name'])
        self.assertEqual([r['number'] for r in records], [2])
        self.assertIn('head=participant%3Asubmission%2Ftest', gh.call_args.args[3])

    def test_status_accepts_only_the_recorded_fork_and_exact_commit(self):
        pr = {'head': {'sha': 'a' * 40, 'repo': {'full_name': self.FORK['full_name']}},
              'base': {'ref': 'main', 'repo': {'full_name': cli.REPO}}}
        cli.validate_pr(pr, 'a' * 40, self.FORK['full_name'])
        for head, repo in [('b' * 40, self.FORK['full_name']), ('a' * 40, 'other/forshor-submissions')]:
            with self.assertRaises(cli.UserError):
                cli.validate_pr(pr, head, repo)


class PublicationIdentity(unittest.TestCase):
    def setUp(self):
        self.submission = {'pr': 12, 'head': 'a' * 40, 'url': 'https://github.com/example/pull/12'}
        self.verifier = {'id': 123, 'run_attempt': 2, 'head_sha': 'a' * 40, 'html_url': 'https://github.com/example/actions/runs/123'}
        self.source = {'run': dict(self.verifier), 'pr': {'number': 12, 'headSha': 'a' * 40}}
        self.publisher = {'id': 456, 'run_attempt': 1, 'conclusion': 'success', 'html_url': 'https://github.com/example/actions/runs/456'}

    def test_receipt_must_match_verifier_attempt_pr_and_commit(self):
        self.assertTrue(cli.publication_matches(self.source, self.submission, self.verifier))
        for section, key, value in [('run', 'id', 999), ('run', 'run_attempt', 1),
                                    ('run', 'head_sha', 'b' * 40), ('pr', 'number', 13),
                                    ('pr', 'headSha', 'b' * 40)]:
            with self.subTest(key=key):
                altered = json.loads(json.dumps(self.source))
                altered[section][key] = value
                self.assertFalse(cli.publication_matches(altered, self.submission, self.verifier))

    def test_unrelated_success_does_not_mean_our_submission_published(self):
        self.source['pr']['number'] = 88
        self.assertIsNone(cli.publication_result(self.publisher, self.source, {'snapshotId': 'c' * 64}, self.submission, self.verifier))

    def test_success_without_a_receipt_is_not_publication(self):
        with self.assertRaisesRegex(cli.UserError, 'without a publication receipt'):
            cli.publication_result(self.publisher, self.source, None, self.submission, self.verifier)

    def test_a_failed_hosted_verification_does_not_blame_the_submission(self):
        message = cli.hosted_failure({'conclusion': 'failure', 'html_url': self.verifier['html_url']})
        self.assertIn(self.verifier['html_url'], message)
        self.assertIn('did not pass (failure)', message)
        self.assertIn('hosted environment', message)
        self.assertIn('./forshor check', message)
        self.assertNotIn('Fix the submission', message)

    def test_failed_publication_directs_recovery_to_publisher_not_lean(self):
        self.publisher['conclusion'] = 'failure'
        with self.assertRaisesRegex(cli.UserError, 'maintainer can rerun its failed publishing job without repeating Lean'):
            cli.publication_result(self.publisher, self.source, None, self.submission, self.verifier)

    def duplicate_receipt(self, **over):
        found = {'policyId': 'e' * 40, 'login': 'tester', 'pull': 92, 'createdAt': '2026-11-15T14:50:23Z', 'sameParticipant': True}
        found.update(over)
        return {'refused': True, 'stage': 'duplicate', 'reason': 'x', 'duplicateOf': found}

    def test_a_repeated_policy_is_reported_as_such_not_as_a_failed_publication(self):
        self.publisher['conclusion'] = 'failure'
        with self.assertRaises(cli.UserError) as caught:
            cli.publication_result(self.publisher, self.source, self.duplicate_receipt(), self.submission, self.verifier)
        message = str(caught.exception)
        self.assertIn('already submitted this exact policy', message)
        self.assertIn('#92', message)
        self.assertIn('2026-11-15', message)
        self.assertNotIn('Publication failed', message)
        self.assertNotIn('rerun', message)

    def test_a_repeat_the_rules_refuse_for_another_participant_does_not_say_you_did_it(self):
        self.publisher['conclusion'] = 'failure'
        with self.assertRaises(cli.UserError) as caught:
            cli.publication_result(self.publisher, self.source, self.duplicate_receipt(sameParticipant=False), self.submission, self.verifier)
        self.assertIn('already published', str(caught.exception))
        self.assertNotIn('you already submitted', str(caught.exception))

    def test_a_malformed_duplicate_receipt_falls_back_to_the_ordinary_failure(self):
        self.publisher['conclusion'] = 'failure'
        for bad in ({'policyId': 'zz'}, {'pull': 'x'}, {'pull': -1}, {'createdAt': '\x1b[2Jboo'}):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(cli.UserError, 'Publication failed'):
                    cli.publication_result(self.publisher, self.source, self.duplicate_receipt(**bad), self.submission, self.verifier)

    def test_completed_replay_is_a_success_without_a_new_snapshot(self):
        result = cli.publication_result(self.publisher, self.source, {'alreadyPublished': True}, self.submission, self.verifier)
        self.assertEqual(result['stage'], 'published')
        self.assertTrue(result['alreadyPublished'])

    def test_a_policy_publication_names_its_row_and_boards_and_is_matched_the_same_way(self):
        receipt = {'schema': 'forshor.publication/v2', 'kind': 'policy', 'snapshotId': 'd' * 64, 'name': 'tester_sub3', 'boards': ['full', 'pp']}
        result = cli.publication_result(self.publisher, self.source, receipt, self.submission, self.verifier)
        self.assertEqual((result['stage'], result['kind'], result['name'], result['boards']), ('published', 'policy', 'tester_sub3', ['full', 'pp']))
        self.assertEqual(result['snapshotId'], 'd' * 64)
        self.assertTrue(result['submission'].endswith('/#results'), 'a policy is looked at on the page, not in the archive branch')
        self.source['pr']['number'] = 88
        self.assertIsNone(cli.publication_result(self.publisher, self.source, receipt, self.submission, self.verifier), 'another PR\'s receipt is not ours')
        self.source['pr']['number'] = 12
        with self.assertRaisesRegex(cli.UserError, 'without a publication receipt'):
            cli.publication_result(self.publisher, self.source, dict(receipt, snapshotId='nope'), self.submission, self.verifier)

    def test_an_older_receipt_still_reads_as_before(self):
        result = cli.publication_result(self.publisher, self.source, {'snapshotId': 'c' * 64}, self.submission, self.verifier)
        self.assertNotIn('kind', result)
        self.assertIn('submission-results/results/policies/', result['submission'])

    def test_pr_moved_to_another_head_is_superseded(self):
        with self.assertRaisesRegex(cli.UserError, 'another commit'):
            cli.validate_pr({'head': {'sha': 'b' * 40}}, 'a' * 40)


class VerificationFeedback(unittest.TestCase):
    def setUp(self):
        self.submission = {'pr': 12, 'head': 'a' * 40, 'url': 'https://github.com/example/pull/12'}
        self.verification = {'id': 123, 'run_attempt': 2, 'path': cli.VERIFIER, 'event': 'pull_request',
                             'status': 'completed', 'conclusion': 'failure', 'html_url': 'https://github.com/example/actions/runs/123'}
        self.metadata = {'repository': cli.REPO, 'pr_number': '12', 'head_sha': 'a' * 40, 'run_id': '123', 'run_attempt': '2'}

    def test_guard_stops_and_proof_failures_have_distinct_messages(self):
        for stage, detail, expected in (
            ('memory', 'killed: memory 12.04 GB > cap 12.0 GB', 'Stopped: memory limit reached'),
            ('replay', 'no trusted output (killed: memory 10.14 GB > cap 10.0 GB)', 'Stopped: memory limit reached'),
            ('timeout', 'stage deadline', 'Stopped: time limit reached'),
            ('cert', 'killed: timeout 900 s', 'Stopped: time limit reached'),
            ('cert', 'type mismatch', 'Proof check failed'),
        ):
            with self.subTest(stage=stage, detail=detail):
                result = {'status': 'failure', 'checks': [{'name': stage, 'status': 'failure', 'details': detail}]}
                message = cli.hosted_failure(self.verification, result)
                self.assertIn(expected, message)
                self.assertIn(self.verification['html_url'], message)
                if 'memory' in expected:
                    self.assertIn("We're working towards supporting submissions that need more memory", message)
                    self.assertIn('not a verdict', message)
                    self.assertNotIn('fix it', message)

    def test_feedback_requires_the_exact_repository_commit_pr_run_and_attempt(self):
        result = {'status': 'failure', 'checks': [{'name': 'memory', 'status': 'failure'}], 'metadata': self.metadata}

        def download(args, **kwargs):
            self.assertIn('table-generation-submission-123-2', args)
            (Path(args[args.index('--dir') + 1]) / 'table-generation-submission-result.json').write_text(json.dumps(result))
            return SimpleNamespace(returncode=0)

        with patch.object(cli, 'run', side_effect=download):
            self.assertEqual(cli.verification_feedback(self.verification, self.submission), result)
            for key in self.metadata:
                with self.subTest(key=key):
                    result['metadata'] = dict(self.metadata, **{key: 'wrong'})
                    self.assertIsNone(cli.verification_feedback(self.verification, self.submission))
        with patch.object(cli, 'run', return_value=SimpleNamespace(returncode=1)):
            self.assertIsNone(cli.verification_feedback(self.verification, self.submission))

    def test_status_distinguishes_waiting_job_from_active_job(self):
        pr = {'head': {'sha': self.submission['head'], 'repo': {'full_name': cli.REPO}},
              'base': {'ref': 'main', 'repo': {'full_name': cli.REPO}}}
        self.verification.update(status='in_progress', conclusion=None)
        for state, expected in [('queued', 'Queued'), ('in_progress', 'Verifying')]:
            responses = [pr, {'workflow_runs': [self.verification]},
                         {'jobs': [{'name': 'Verify submission with Lean', 'status': state}]}]
            with self.subTest(state=state), patch.object(cli, 'api', side_effect=responses) as api:
                status = cli.inspect_status(self.submission, {})
                self.assertTrue(status['stage'].startswith(expected))
                self.assertEqual(api.call_args.args[0], 'actions/runs/123/attempts/2/jobs?per_page=100')

    def test_status_uses_failed_attempt_diagnostics(self):
        pr = {'head': {'sha': self.submission['head'], 'repo': {'full_name': cli.REPO}},
              'base': {'ref': 'main', 'repo': {'full_name': cli.REPO}}}
        feedback = {'status': 'failure', 'checks': [{'name': 'memory', 'status': 'failure'}]}
        with patch.object(cli, 'api', side_effect=[pr, {'workflow_runs': [self.verification]}]), \
                patch.object(cli, 'verification_feedback', return_value=feedback) as read:
            with self.assertRaisesRegex(cli.UserError, 'Stopped: memory limit reached'):
                cli.inspect_status(self.submission, {})
            read.assert_called_once_with(self.verification, self.submission)


class ToolchainSetup(unittest.TestCase):
    def test_existing_toolchain_is_reused_and_environment_is_pinned(self):
        with patch.object(cli, 'git', return_value='leanprover/lean4:v4.28.0'), \
                patch.object(cli.shutil, 'which', return_value='/bin/elan'), \
                patch.object(cli, 'run', return_value=SimpleNamespace(stdout='leanprover/lean4:v4.28.0 (default)\n')), \
                patch.object(cli, 'logged') as logged:
            env = cli.lean_environment(Path('/tmp'), 'a' * 40)
            self.assertEqual(env['ELAN_TOOLCHAIN'], 'leanprover/lean4:v4.28.0')
            self.assertEqual(logged.call_count, 1)
            self.assertEqual(logged.call_args.args[2], ['elan', 'run', 'leanprover/lean4:v4.28.0', 'lean', '--version'])

    def test_missing_pinned_version_is_installed_without_changing_default(self):
        with patch.object(cli, 'git', return_value='leanprover/lean4:v4.28.0'), \
                patch.object(cli.shutil, 'which', return_value='/bin/elan'), \
                patch.object(cli, 'run', return_value=SimpleNamespace(stdout='leanprover/lean4:v4.30.0\n')), \
                patch.object(cli, 'logged') as logged:
            cli.lean_environment(Path('/tmp'), 'a' * 40)
            self.assertEqual(logged.call_args_list[0].args[2], ['elan', 'toolchain', 'install', 'leanprover/lean4:v4.28.0'])
            self.assertEqual(logged.call_count, 2)


if __name__ == '__main__':
    unittest.main()
