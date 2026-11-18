# MIS-195 cap check

Six cases, each on three fresh standard public `ubuntu-24.04` runners. The
approved comparison is simple, demanding with the same band count, and demanding
expanded to 256 bands, independently for PhaseProduct and PhaseTripleProduct.

| Case | Bands | Tables | Operations across bands |
| --- | ---: | --- | ---: |
| pp-simple | 2 | k=4 from #107 | 64 |
| pp-heavy | 2 | k=8/k=4 from #107 | 169 |
| pp-heavy-256 | 256 | same tables as pp-heavy | 34,967 |
| ptp-simple | 2 | k=4 from #109 | 80 |
| ptp-heavy | 2 | k=8/k=4 from #109 | 234 |
| ptp-heavy-256 | 256 | same tables as ptp-heavy | 49,510 |

The two-band thresholds are 1024 and 128 in every case. Expanded cases add
thresholds 1278 through 1025, all selecting the original top table. The selected
table at every width is unchanged. No identity-operation padding or invented
large integers are used. Table correctness is checked by the production Python
parser, then the production converter emits ordinary Lean proofs. The unchanged
full verifier determines acceptance. A failure is retained, not edited away.
The original #107/#109 source and canonical policy digests bind the input tables.
All six files are regenerated. `ptp-heavy` reproduces #109 byte-for-byte; the
other five are derived cases. Exact #107/#109 source replays were already
measured three times in run 44179672801.

`manifest.json` pins current verifier `356f3d518c0a5c15e370f06ae63c237b582ee040`,
ForShor `349849e091fc05a4e80c99ae3511985faf3b8843`, and the unchanged scientific
epoch `ab1f8887b1b50e56`. The production Lean/engine composite actions restore
read-only caches and prepare swap. Lean uses one thread. No engine or Lean cache
input is modified. Every run uses the current 12 GiB mirror cap, 600-second
mirror timeout, and 900-second individual mirror stage timeout. The 30-minute
workflow limit is unchanged. An outer observer stops 90 seconds before the job
budget expires to retain diagnostics; report that outcome as a benchmark guard,
not an observed production timeout. Setup time reduces the remaining budget.

The external observer samples stage process-tree RSS+swap every 250 ms and
PSS+SwapPss approximately every second. It additionally samples the entire intake
process tree, including Python stages and extraction output buffering. It never
patches the verifier. These are sampled peaks, not exact maxima. Original memcap
records, host RAM/CPU/toolchain, policy/source identity and verification evidence
are retained. Capped or timed-out measurements are censored. Bootstrap memory is
not separately measured. The observer introduces some timing overhead.

## Publication isolation

The branch-only workflow is named `MIS-195 cap check`, absent from the production
publisher and admission workflow triggers. Its event is `push`, not a submission
PR. It calls intake with `--no-publish`, grants only `contents: read`, disables
checkout credential persistence, and unsets publishing/GitHub tokens before
verification. It has no Google authentication step. Logs and diagnostic artifacts
are public GitHub Actions evidence; no test submission is published or archived
on the submission-results branch. Production caps and runner settings stay fixed.

## Reproduce

The exact workflow is `.github/workflows/cap-check.yml`. Rerun its recorded run:

```sh
gh run rerun RUN_ID --repo zapatacomputing/forshor-submissions
gh run download RUN_ID --repo zapatacomputing/forshor-submissions --pattern 'capcheck-*-RUN_ID-ATTEMPT' --dir downloaded
python3 benchmarks/memory/summarize.py downloaded --out summary.json
```

The matrix has six cases and repeats 1, 2, 3, at most six jobs concurrently.
Three repeats test variability in this comparison. Passing these cases does not
guarantee all permitted programs fit or establish that 256 is the right policy
limit. The benchmark tests verification; publication-only duplicate/identity
rules and per-account quotas are inspected separately, without publishing.

## Targeted sorting-proof diagnostic

The original six-case suite is frozen at commit
`8cd5b2773734fb9ca8b2c25eb96b0b75eabfa606`, run `44201207153`.
Its 256-band PP source hits Lean's recursion limit at `sorted := by decide`.
The follow-up matrix runs only `pp-heavy-256-kernel`, three times: identical
tables, thresholds and canonical policy digest, replacing that one sorting proof
with `decide +kernel`, already used by the same template for table proofs. This
does not disable kernel checking or change any verifier/library/engine input.
It tests whether a memory/time restriction also appears after the source proof
obstacle. Keep the original failures in all reports.

Acceptance is determined from `intake-result.json` and `observations.json`, not
the GitHub job's green/red status. The original console `tee` pipeline did not
propagate a verifier refusal to the job exit code. This follow-up explicitly
enables `pipefail`; original artifacts still record refusals and exit code 1.
