"""The policy verifier: which files it takes, what only the submission file may be, and how the intake's three outcomes become the result `./forshor check` reads.
The intake itself (Lean, the mirror, ForShor) is stubbed: it is tested in evaluation/export_v2/pytests/test_intake_v2.py and was run for real on both boards."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import policy_verifier as pv  # noqa: E402

PP = "import ForShor.X\nnamespace PolicySubmission\ndef policy := 1\nend PolicySubmission\n"
PTP = "import ForShor.X\nnamespace PTPPolicySubmission\ndef policy := 1\nend PTPPolicySubmission\n"
OLD = "import TableGeneration.Policy\nnamespace TableGeneration.Submission.Policy\ndef implementation := 1\nend TableGeneration.Submission.Policy\n"


def stub(tmp: Path, outcome: dict) -> list:
    """A command that stands in for evaluation/intake_v2.py: writes `outcome` where --out says and a verification where --verification-out says."""
    script = tmp / "stub_intake.py"
    script.write_text("import json, sys\n"
                      "a = sys.argv\n"
                      f"o = {outcome!r}\n"
                      "open(a[a.index('--out') + 1], 'w').write(json.dumps(o))\n"
                      "if o.get('verified'):\n"
                      "    open(a[a.index('--verification-out') + 1], 'w').write('{}')\n"
                      "open('args.json', 'w').write(json.dumps(a))\n"
                      "sys.exit(0 if o.get('verified') else 1)\n")
    return [sys.executable, str(script)]


class PolicyVerifier(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.tmp = Path(self.t.name)
        self.repo = self.tmp / "repo"
        (self.repo / "TableGeneration/Submission").mkdir(parents=True)
        self.out = self.tmp / "out"
        (self.tmp / "forshor").mkdir()

    def tearDown(self):
        self.t.cleanup()

    def write(self, text):
        (self.repo / pv.SUBMISSION).write_text(text)

    def run_pv(self, outcome=None, files=None, ok=True, env=None, preflight=False):
        changed = lambda repo: (ok, [pv.SUBMISSION] if files is None else files, "Compared base...head.")
        env = {"FORSHOR_TREE": str(self.tmp / "forshor")} if env is None else env
        code = pv.run(self.repo, self.out, get_changed_files=changed, env=env, intake=stub(self.tmp, outcome or {"verified": True, "policy": {"digest": "a" * 64, "mode": "PhaseProduct", "bands": 1, "forshorPolicy": True}}), preflight=preflight)
        return code, json.loads((self.out / "table-generation-submission-result.json").read_text())

    def test_the_two_policy_namespaces_are_this_verifiers_and_the_older_file_is_not(self):
        for text, expected in ((PP, True), (PTP, True), (OLD, False), ("def x := 1\n", False)):
            self.write(text)
            self.assertEqual(pv.applies(self.repo), expected, text[:40])
        self.assertFalse(pv.applies(self.tmp / "nowhere"))

    def test_a_verified_policy_is_a_successful_full_result_with_its_verification(self):
        self.write(PP)
        code, result = self.run_pv()
        self.assertEqual((code, result["status"], result["phase"], result["kind"]), (0, "success", "full", "policy"))
        self.assertEqual(result["verification"], "verification.json")
        self.assertTrue((self.out / "verification.json").exists() and (self.out / "table-generation-summary.md").exists())
        args = json.loads((self.tmp / "repo" / "args.json").read_text())
        self.assertIn("--no-publish", args)
        self.assertEqual(args[args.index("--lean-policy") + 1], str(self.repo.resolve() / pv.SUBMISSION))
        self.assertEqual(args[args.index("--tree") + 1], str(self.repo.resolve()), "the STO tree defaults to the repository")
        self.assertNotIn("--publisher-url", args, "the verifier never publishes and needs no publisher")

    def test_a_refusal_names_its_stage_and_a_failed_run_is_not_called_a_verdict(self):
        self.write(PP)
        code, result = self.run_pv({"refused": True, "stage": "lexical", "reason": "ForShor's lexical gate refused the file"})
        self.assertEqual((code, result["status"]), (1, "failure"))
        self.assertEqual(result["checks"][-1]["name"], "lexical")
        self.assertIsNone(result["verification"])
        code, result = self.run_pv({"failed": True, "error": "OSError: no space left"})
        self.assertEqual(code, 1)
        self.assertIn("not a verdict", result["checks"][-1]["details"])

    def test_only_the_submission_file_may_change(self):
        self.write(PP)
        for extra in ("TableGeneration/Submission/Policy/Helper.lean", "lakefile.lean", "README.md"):
            code, result = self.run_pv(files=[pv.SUBMISSION, extra])
            self.assertEqual(code, 1, extra)
            self.assertIn(extra, result["checks"][0]["details"])
        code, _ = self.run_pv(files=[])
        self.assertEqual(code, 0, "no file list (a local run with no base) is the comparison skipped, said so, not a failure here")
        code, result = self.run_pv(ok=False)
        self.assertEqual(code, 1)
        self.assertEqual(result["checks"][0]["status"], "failure")

    def test_memory_stop_is_visible_in_the_result_and_run_summary(self):
        self.write(PTP)
        code, result = self.run_pv({"refused": True, "stage": "memory", "reason": "killed: memory 12.04 GB > cap 12.0 GB"})
        self.assertEqual(code, 1)
        self.assertEqual(result["checks"][-1]["name"], "memory")
        summary = (self.out / "table-generation-summary.md").read_text()
        self.assertIn("Stopped: memory limit reached", summary)
        self.assertIn("This is not a verdict on your policy", summary)
        self.assertIn("We're working towards supporting submissions that need more memory", summary)

    def test_the_trusted_environment_must_be_prepared(self):
        self.write(PP)
        code, result = self.run_pv(env={})
        self.assertEqual(code, 1)
        self.assertEqual(result["checks"][-1]["name"], "trees")

    def test_result_is_bound_to_the_pr_head_and_exact_hosted_attempt(self):
        self.write(PP)
        env = {"FORSHOR_TREE": str(self.tmp / "forshor"), "GITHUB_REPOSITORY": "zapatacomputing/forshor-submissions",
               "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "TABLE_GEN_PR_NUMBER": "12", "TABLE_GEN_HEAD_SHA": "a" * 40}
        code, result = self.run_pv(env=env)
        self.assertEqual(code, 0)
        self.assertEqual(result["metadata"]["head_sha"], "a" * 40)
        self.assertEqual(result["metadata"]["run_id"], "123")
        self.assertEqual(result["metadata"]["run_attempt"], "2")
        self.assertEqual(result["metadata"]["pr_number"], "12")

    def test_the_older_syntax_gate_steps_aside_for_a_policy_file_and_only_for_that(self):
        """CI runs the older syntax-gate script before the verifier, in the submission's directory; it must not fail a policy submission."""
        import subprocess
        script = Path(__file__).parent / "test_submission_syntax_gate.py"
        self.write(PP)
        done = subprocess.run([sys.executable, str(script)], cwd=self.repo, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("not applicable to a policy submission", done.stdout)

    def test_the_preflight_checks_the_files_and_runs_no_lean(self):
        self.write(PP)
        code, result = self.run_pv(env={}, preflight=True)
        self.assertEqual((code, result["phase"]), (0, "preflight"))
        self.assertFalse((self.tmp / "repo" / "args.json").exists(), "nothing was run")
        code, _ = self.run_pv(files=[pv.SUBMISSION, "x"], env={}, preflight=True)
        self.assertEqual(code, 1)


HOSTILE = ("\\documentclass{article}\n\\immediate\\write18{touch CANARY}\n\\input{/etc/passwd}\n@everyone <script>alert(1)</script> ```\n" + "x" * 60000 + "\r\n\tend\n")


def carrying_stub(tmp: Path, wrong: bool = False) -> list:
    """An intake stand-in that behaves like the real one with --summary: the verification carries the hash of the file it was given (`wrong`: of another file)."""
    script = tmp / "stub_intake2.py"
    script.write_text("import hashlib, json, sys\n"
                      "a = sys.argv\n"
                      "digest = hashlib.sha256(open(a[a.index('--summary') + 1], 'rb').read() + (b'x' if " + repr(wrong) + " else b'')).hexdigest() if '--summary' in a else None\n"
                      "o = {'verified': True, 'policy': {'digest': 'a' * 64, 'mode': 'PhaseProduct', 'bands': 1, 'forshorPolicy': True}}\n"
                      "open(a[a.index('--out') + 1], 'w').write(json.dumps(o))\n"
                      "open(a[a.index('--verification-out') + 1], 'w').write(json.dumps({'summarySha256': digest}))\n"
                      "open('args.json', 'w').write(json.dumps(a))\n")
    return [sys.executable, str(script)]


class OptionalSummary(unittest.TestCase):
    """The optional TableGeneration/Submission/summary.tex: the rules are enforced here (never by compiling), it rides along to the intake as a path, it cannot be submitted
    alone, and nothing else may be added beside it."""

    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.tmp = Path(self.t.name)
        self.repo = self.tmp / "repo"
        (self.repo / "TableGeneration/Submission").mkdir(parents=True)
        (self.repo / pv.SUBMISSION).write_text(PP)
        self.out = self.tmp / "out"
        (self.tmp / "forshor").mkdir()
        self.env = {"FORSHOR_TREE": str(self.tmp / "forshor")}

    def tearDown(self):
        self.t.cleanup()

    def summary_path(self):
        return self.repo / pv.SUMMARY

    def run_pv(self, files=None, intake=None, preflight=False, env=None):
        files = [pv.SUBMISSION, pv.SUMMARY] if files is None else files
        code = pv.run(self.repo, self.out, get_changed_files=lambda repo: (True, files, "Compared base...head."), env=self.env if env is None else env,
                      intake=intake or carrying_stub(self.tmp), preflight=preflight)
        return code, json.loads((self.out / "table-generation-submission-result.json").read_text())

    def test_the_summary_path_is_the_agreed_one_and_the_rules_are_the_shared_module(self):
        self.assertEqual(pv.SUMMARY, "TableGeneration/Submission/summary.tex")
        self.assertIs(pv.summary_tex.problem, __import__("summary_tex").problem)

    def test_policy_only_is_unchanged_and_runs_no_summary_option(self):
        code, result = self.run_pv(files=[pv.SUBMISSION], intake=stub(self.tmp, {"verified": True, "policy": {"digest": "a" * 64, "mode": "PhaseProduct", "bands": 1, "forshorPolicy": True}}))
        self.assertEqual((code, result["status"]), (0, "success"))
        self.assertIsNone(result["summary"])
        self.assertNotIn("--summary", json.loads((self.repo / "args.json").read_text()))
        self.assertEqual([c["name"] for c in result["checks"]], ["changed files", "policy"], "no summary check is reported when there is no summary")

    def test_policy_and_summary_pass_the_file_by_path_and_record_its_hash(self):
        self.summary_path().write_bytes(HOSTILE.encode())
        code, result = self.run_pv()
        self.assertEqual((code, result["status"]), (0, "success"), result["checks"])
        args = json.loads((self.repo / "args.json").read_text())
        self.assertEqual(args[args.index("--summary") + 1], str(self.repo.resolve() / pv.SUMMARY))
        import hashlib
        self.assertEqual(result["summary"], {"sha256": hashlib.sha256(HOSTILE.encode()).hexdigest(), "bytes": len(HOSTILE.encode())})
        self.assertEqual([c["name"] for c in result["checks"]], ["changed files", "summary", "policy"])
        self.assertIn("never compiled or run", result["checks"][1]["details"])

    def test_a_summary_alone_is_not_a_submission(self):
        self.summary_path().write_text("hello\n")
        code, result = self.run_pv(files=[pv.SUMMARY])
        self.assertEqual(code, 1)
        self.assertEqual(result["checks"][0]["name"], "changed files")
        self.assertTrue(result["checks"][0]["details"].startswith("a submission needs a policy"), result["checks"][0]["details"])
        self.assertFalse((self.repo / "args.json").exists(), "nothing ran")

    def test_nothing_else_may_be_added_beside_the_pair(self):
        self.summary_path().write_text("hello\n")
        for extra in ("TableGeneration/Submission/notes.tex", "TableGeneration/Submission/Policy/summary.tex", "TableGeneration/Submission/Policy/Helper.lean", "README.md",
                      ".github/workflows/x.yml", "TableGeneration/Submission/Summary.tex"):
            code, result = self.run_pv(files=[pv.SUBMISSION, pv.SUMMARY, extra])
            self.assertEqual(code, 1, extra)
            self.assertTrue(result["checks"][0]["details"].startswith("only TableGeneration/Submission/Policy.lean"), result["checks"][0]["details"])
            self.assertIn(extra, result["checks"][0]["details"])
            self.assertEqual(len(result["checks"]), 1, "the summary is not even looked at when the file list is wrong")

    def test_each_rule_names_itself_and_stops_before_any_lean(self):
        cases = {"symlink": "symbolic link", "directory": "not a directory", "oversize": "limit is 102,400", "latin1": "must be UTF-8", "nul": "U+0000", "escape": "U+001B", "empty": "is empty"}
        for name, expected in cases.items():
            path = self.summary_path()
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
            if name == "symlink":
                (self.tmp / "real.tex").write_text("fine\n")
                path.symlink_to(self.tmp / "real.tex")
            elif name == "directory":
                path.mkdir()
            else:
                path.write_bytes({"oversize": b"a" * 102401, "latin1": b"caf\xe9\n", "nul": b"a\x00b\n", "escape": b"a\x1b[31mb\n", "empty": b""}[name])
            (self.repo / "args.json").unlink(missing_ok=True)
            code, result = self.run_pv()
            self.assertEqual(code, 1, name)
            failing = [c for c in result["checks"] if c["status"] == "failure"]
            self.assertEqual([c["name"] for c in failing], ["summary"], name)
            self.assertIn(expected, failing[0]["details"], name)
            self.assertIn("summary.tex", failing[0]["details"])
            self.assertFalse((self.repo / "args.json").exists(), f"{name}: the intake did not run")
            self.assertIsNone(result["verification"])

    def test_a_deleted_summary_in_the_file_list_is_refused(self):
        code, result = self.run_pv()           # listed as changed, absent on disk
        self.assertEqual(code, 1)
        self.assertIn("deleted or renamed", result["checks"][-1]["details"])

    def test_the_preflight_checks_the_summary_too_and_runs_no_lean(self):
        self.summary_path().write_bytes(b"a\x00")
        code, result = self.run_pv(preflight=True, env={})
        self.assertEqual((code, result["phase"]), (1, "preflight"))
        self.assertEqual(result["checks"][-1]["name"], "summary")
        self.summary_path().write_text("fine\n")
        code, result = self.run_pv(preflight=True, env={})
        self.assertEqual((code, result["phase"]), (0, "preflight"))

    def test_a_verification_that_does_not_carry_the_checked_file_is_not_a_success(self):
        self.summary_path().write_text("hello\n")
        code, result = self.run_pv(intake=carrying_stub(self.tmp, wrong=True))
        self.assertEqual(code, 1)
        self.assertIn("does not carry the summary", result["checks"][-1]["details"])
        self.assertIsNone(result["verification"])

    def test_the_summary_is_never_compiled_or_executed(self):
        """The only subprocess the verifier starts is the intake command; the TeX in the file is not run, and the intake is handed a path, never the text."""
        self.summary_path().write_bytes(HOSTILE.encode())
        started = []
        real = pv.subprocess.run

        def recording(cmd, *a, **k):
            started.append(list(cmd))
            return real(cmd, *a, **k)
        pv.subprocess.run = recording
        try:
            code, _ = self.run_pv()
        finally:
            pv.subprocess.run = real
        self.assertEqual(code, 0)
        self.assertEqual(len(started), 1, started)
        self.assertEqual(Path(started[0][1]).name, "stub_intake2.py")
        self.assertFalse(any("tex" in Path(part).name.lower() and Path(part).name != "summary.tex" for part in started[0][:2]), "no TeX engine")
        self.assertFalse(any("write18" in part for part in started[0]), "the text itself is not in the command line")
        self.assertFalse(list(self.tmp.rglob("CANARY")), "nothing ran the \\write18 line")


if __name__ == "__main__":
    unittest.main()
