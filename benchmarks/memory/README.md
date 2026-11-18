# MIS-195 band-count sweep

This follow-up varies only band count for the demanding PP/PTP tables already
measured in runs 44201207153 and 44204070860. The two-band versions pass; the
256-band versions fail. The real 77-band #107, with a different mix of tables,
also passed the earlier run 44179672801. This sweep must not be interpreted as a
universal band-count bound or a reason to reject that successful real submission.

## Fixed configuration

- Verifier main `640522c5360b21aa80bdc121c5e697a9c9b25832` (PR #116).
- ForShor `349849e091fc05a4e80c99ae3511985faf3b8843`.
- Scientific epoch `ab1f8887b1b50e56`, unchanged engine, baseline and Lean inputs.
- Standard public `ubuntu-24.04` x64, four vCPUs, production Lean/Python caches
  and swap preparation; one Lean thread. No paid larger runner.
- Current production budgets: 60-minute verification job, 600-second overall
  mirror timeout, 900-second individual mirror stage setting, 12 GiB replay cap.
  PR #116 changes the outer job limit and admission accounting only; all
  mathematical checks, memory and inner mirror time limits match the prior study.
- An observer guard reserves 90 seconds before the job deadline for evidence.
  Guard terminations are censored benchmark outcomes, not proof that a policy
  would exhaust production's full deadline.

## Cases and search

The first batch tries 16, 64 and 128 bands for both families, once each. Subsequent
batches narrow the useful passing/failing range and repeat relevant counts three
times on fresh runners. `manifest.json` pins every source policy and chosen count.

Each policy has the same k=8 top table and k=4 fallback as the prior demanding
case, drawn from accepted #107/#109 tables. The original thresholds are 1024 and
128. A B-band case splits the top interval at 1024 through 1024+B-2, selecting
that same k=8 table in each interval. Mathematical dispatch at EVERY width is
unchanged; table operations and numerical values do not change. The normal
trusted converter emits the policy and its sorting proof (`by decide` for PP).
No alternative proof or input normalization is silently substituted after failure.

The trusted Python parser checks all tables, the source gate runs, then the full
unchanged intake verifies the Lean source with `--no-publish`. RSS+swap is sampled
at 250 ms and PSS+SwapPss approximately every second for each Lake stage and the
whole intake tree. Compilation and extraction may exceed the replay-only guard;
this is measured, not patched. Original memcap records and complete verifier
outcomes are retained. Peaks from failed runs are censored.

## Publication isolation and reproduction

The branch-only workflow is named `MIS-195 band sweep`, absent from publisher
and admission triggers. It grants only contents:read, disables checkout credential
persistence, contains no Google authentication and unsets publishing/GitHub tokens
before verifying. No submission PR is created. Public Actions logs/artifacts are
diagnostics; no test policy is published or archived as a challenge submission.

Re-run an exact recorded batch, download one attempt and summarize:

```sh
gh run rerun RUN_ID --repo zapatacomputing/forshor-submissions
gh run download RUN_ID --repo zapatacomputing/forshor-submissions --pattern 'bandsweep-*-RUN_ID-ATTEMPT' --dir downloaded
python3 benchmarks/memory/summarize.py downloaded --out summary.json
```

The exact `.github/workflows/band-sweep.yml` at each run's commit defines its
matrix. At most six jobs run concurrently. Source/digest/epoch, actual RAM, CPU,
architecture, image and toolchain are recorded in each artifact. Acceptance comes
from intake-result.json, not merely the GitHub job state. Three passes provide
repeatability evidence for that construction, not a guarantee for arbitrary
programs with the same number of bands.
