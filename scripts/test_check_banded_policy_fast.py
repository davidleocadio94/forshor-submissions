#!/usr/bin/env python3
"""`check_banded_policy.py --fast`: the PhaseProduct harness without its interpreted report stage.

The report stage is diagnostics only (Lean's interpreter never memoises the closures a `State` is built from, so its cost doubles every
~10 operations: hours for a deep k = 8 table). In fast mode the exact ladder literal comes from `exact_width.exact_ladder`, an
untrusted Python transcription, and the certificate re-proves it. These tests pin what can be pinned without Lean:

* the Python ladder equals the ladder the KERNEL proved, on every PhaseProduct policy that has accepted evidence in the archive;
* `--fast` needs `--widths exact`;
* fast mode never runs the report, hands the Python ladder to the certificate, and keeps the rejection stages
  (a failing certificate is still a rejection, and the threshold rule is named by the Python mirror).

The kernel side (a WRONG literal makes the certificate not build; a fast run and a full run agree) is covered by real Lean runs, see
the pull request that added the option.

Run: python3 -m pytest scripts/test_check_banded_policy_fast.py
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_banded_policy as cbp  # noqa: E402
from exact_width import exact_ladder  # noqa: E402

EVIDENCE = ROOT / "evaluation" / "export_v2" / "evidence-archive"


def _kernel_proved_pp():
    out = []
    for f in sorted(EVIDENCE.glob("*/*.evidence.json")):
        env = json.loads(f.read_text())
        rec = env["record"]
        if rec.get("tool") != "check_banded_policy" or rec.get("verdict") != "accepted" or not (rec.get("audit") or {}).get("exact_ladder"):
            continue
        pol = json.loads(f.with_name(f.name.replace(".evidence.json", ".policy.json")).read_text())
        out.append(pytest.param(pol, rec["exact_ladder"], id=f.name[:16]))
    return out


PROVED = _kernel_proved_pp()


def test_there_is_kernel_proved_evidence_to_compare_with():
    assert len(PROVED) >= 5


@pytest.mark.parametrize("policy,proved", PROVED)
def test_the_python_ladder_equals_the_kernel_proved_one(policy, proved):
    assert exact_ladder(policy, 2048) == proved


def test_fast_needs_exact_widths(tmp_path):
    (tmp_path / "p.json").write_text("{}")
    with pytest.raises(SystemExit) as e:
        cbp.main([str(tmp_path / "p.json"), "--target-width", "2048", "--fast"])
    assert e.value.code == 2


class FakeRun:
    """subprocess.run, replaced: `cp` makes the directory, the data build passes, the certificate build fails with `cert_out`;
    anything else (above all the report: `lean --run scripts/BandedPolicyReport.lean`) is a test failure."""

    def __init__(self, cert_out):
        self.cert_out, self.cmds = cert_out, []

    def __call__(self, cmd, **kw):
        self.cmds.append(list(cmd))
        if cmd[0] == "pgrep":
            return subprocess.CompletedProcess(cmd, 1, "", "")
        if cmd[0] == "cp":
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["lake", "build", cbp.DATA_MOD]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["lake", "build", cbp.CERT_MOD]:
            return subprocess.CompletedProcess(cmd, 1, self.cert_out, "")
        raise AssertionError(f"unexpected command in fast mode: {cmd}")


def run_fast(monkeypatch, tmp_path, policy, cert_out):
    fake = FakeRun(cert_out)
    monkeypatch.setattr(cbp.subprocess, "run", fake)
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps(policy))
    out = tmp_path / "record.json"
    code = cbp.main([str(pol), "--target-width", "2048", "--widths", "exact", "--fast", "--workdir", str(tmp_path / "work"), "--out", str(out)])
    return code, json.loads(out.read_text()), fake, tmp_path / "work"


PP_POLICY = PROVED[0].values[0] if PROVED else None


def test_fast_mode_skips_the_report_and_gives_the_certificate_the_python_ladder(monkeypatch, tmp_path):
    code, rec, fake, work = run_fast(monkeypatch, tmp_path, PP_POLICY, "error: some other failure")
    assert not any("BandedPolicyReport" in " ".join(c) for c in fake.cmds), "the interpreted report must not run"
    assert rec["seconds"]["report"] == 0.0 and "report stage skipped" in rec["fast"]
    assert rec["exact_ladder"] == exact_ladder(PP_POLICY, 2048)
    cert = (work / cbp.INST / "SubmittedCert.lean").read_text()
    ladder = ", ".join(f"({r['width']}, {'none' if r['k'] is None else 'some ' + str(r['k'])})" for r in rec["exact_ladder"])
    assert ladder in cert, "the certificate carries the Python ladder literal for the kernel to re-prove"
    # a failing certificate is still a rejection, at the cert stage
    assert code == 1 and rec["verdict"] == "rejected" and rec["rejection"]["stage"] == "cert"
    assert "scan_ladder" not in rec


def test_fast_mode_names_a_failed_threshold_rule_with_the_python_mirror(monkeypatch, tmp_path):
    bad = json.loads(json.dumps(PP_POLICY))
    bad["bands"][-1]["threshold"] = 1                    # applied down to width 1: below k, degenerate limbs
    assert cbp.threshold_failure_py(bad, 2048)["kind"] in ("degenerate_limbs", "no_shrink")
    code, rec, _, _ = run_fast(monkeypatch, tmp_path, bad, "error: chain_contracts_tight (by decide +kernel) failed")
    assert code == 1 and rec["rejection"]["stage"] == "threshold"
    assert "threshold too low: band" in rec["rejection"]["reason"] and "(kernel)" in rec["rejection"]["reason"]


def test_a_policy_that_obeys_the_threshold_rule_has_no_python_failure():
    for p in PROVED:
        assert cbp.threshold_failure_py(p.values[0], 2048) is None, p.id


@pytest.mark.parametrize('stage,outcome', [('memory', 'killed: memory 12.04 GB > cap 12.0 GB'),
                                          ('timeout', 'killed: timeout 900 s')])
def test_replay_guard_kills_keep_their_stage(monkeypatch, tmp_path, stage, outcome):
    class GuardRun(FakeRun):
        def __call__(self, cmd, **kw):
            if cmd[:3] == ['lake', 'build', cbp.CERT_MOD]:
                return subprocess.CompletedProcess(cmd, 0, '', '')
            if '--log' in cmd:
                Path(cmd[cmd.index('--log') + 1]).write_text(json.dumps({'outcome': outcome}))
                return subprocess.CompletedProcess(cmd, 1, '', 'compiler noise\n' * 500)
            return super().__call__(cmd, **kw)

    monkeypatch.setattr(cbp.subprocess, 'run', GuardRun(''))
    policy, record = tmp_path / 'policy.json', tmp_path / 'record.json'
    policy.write_text(json.dumps(PP_POLICY))
    code = cbp.main([str(policy), '--target-width', '2048', '--widths', 'exact', '--fast',
                     '--workdir', str(tmp_path / 'work'), '--out', str(record)])
    rejection = json.loads(record.read_text())['rejection']
    assert code == 1
    assert rejection['stage'] == stage
    assert rejection['reason'] == outcome
