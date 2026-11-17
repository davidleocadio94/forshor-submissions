# MIS-195 verification memory benchmark

This branch benchmarks the production verification path. It does not publish
submissions or alter the live runner, cap, verifier, engine, scientific epoch,
baseline evidence, or Lean cache inputs. The workflow name is deliberately absent
from the publisher's `workflow_run` list. No PR or participant branch is needed.

## Fixed inputs

`manifest.json` pins verifier `5e037e0cdf45b59addef57376d7ee7ebcf2b7272`, ForShor
`349849e091fc05a4e80c99ae3511985faf3b8843`, epoch `ab1f8887b1b50e56`, and seven
cases. The exact #107 and #109 files have SHA-256 checks. Four archived seed
policies are rendered with the pinned starter converter. `pp-k8` uses #107's
unaltered table definitions with a two-band policy selecting k=8 and k=4. The
unchanged verifier determines whether each case is accepted.

## Reproduce

The branch-only `.github/workflows/memory-benchmark.yml` runs on a push touching
the workflow or this directory. The matrix lists the selected cases and repeats;
use `[1, 2, 3]` for the measured suite. Maximum concurrency is six, one case per
fresh `ubuntu-24.04` x64 hosted VM, with a 30-minute job limit. The public
repository uses standard runners, with no paid larger runner.

Each job checks out the verifier at its fixed commit into `verifier` and restores
its production Python 3.12 and Lean caches using the unchanged composite actions.
The unchanged preparation script adds 8 GiB swap. Cache saves remain disabled.
`prepare.py` generates the input and runs the production source gate before
compilation. `observe.py` launches exactly the intake command used by policy CI:

```sh
BENCH_RESULTS="$(pwd)/results"
export LEAN_NUM_THREADS=1
unset PUBLISHER_URL PUBLISHER_TOKEN GH_TOKEN GITHUB_TOKEN
python3 benchmark/benchmarks/memory/observe.py --out "$BENCH_RESULTS" -- \
  "$ENGINE_PYTHON" verifier/evaluation/intake_v2.py \
  --lean-policy "$BENCH_RESULTS/Policy.lean" --login memory-benchmark --no-publish \
  --tree "$STO_TREE" --forshor-tree "$FORSHOR_TREE" \
  --workdir "$BENCH_RESULTS/work" --verification-out "$BENCH_RESULTS/verification.json" \
  --out "$BENCH_RESULTS/intake-result.json" --stage-timeout 900 --mirror-timeout 600
```

Run with paths adjusted to your checkout, from the pinned verifier's Lean
project (the workflow has the exact working directory and relative paths).
The source-change admission/quota and publication layers are outside this
verification benchmark. No accepted-verification receipt is reused. The normal
`--fast` mirror path is unchanged: it skips interpreted diagnostic reports while
kernel-checking certificates, exact ladders, theorem statements and axioms.

Download and summarize an exact run:

```sh
gh run download RUN_ID --repo zapatacomputing/forshor-submissions --dir downloaded
python3 benchmarks/memory/summarize.py downloaded --out summary.json
```

## Measurements and limits

`host.json` records actual RAM/swap, OS image, architecture, CPU, tool versions,
runner/run identity and the command. `identity.json` records the source hash,
verifier/ForShor commits and full model/epoch. `verification.json` and the mirror
evidence bind the canonical policy digest and checked theorem statements.

The external observer samples each Lake stage's entire descendant process tree
at 250 ms using `/proc/PID/status` (`VmRSS + VmSwap`) and reads
`smaps_rollup` (`Pss + SwapPss`) about once per second. It records host
`MemAvailable` and swap usage concurrently. No Lake shim, verifier patch, Lean
source instrumentation or engine modification is involved. The observer itself
is outside the measured stage tree. Reading page tables has some overhead;
these durations describe instrumented runs, not a controlled speed comparison.

The unmodified cap checker independently polls every second and records rounded
GiB values. `collect.py` retains its records for both compilation and replay,
including failed attempts. Prefer those records when describing cap enforcement;
use the faster external samples for headroom and stage comparisons. A sampled
peak can miss shorter spikes, and an enforced-cap failure is a censored
measurement, not the unrestricted peak. Stage durations from the observer have
roughly 250 ms endpoint precision; successful mirror records also carry their
own wall times. The benchmark has an additional 28-minute outer command guard;
the production job's 30-minute total bound and internal timeouts remain intact.

RSS counts resident mappings, including shared pages; summing it is not unique
physical memory. PSS divides shared pages among mappings, and SwapPss similarly
apportions swap. Neither is interchangeable with a single process's maximum RSS,
macOS `top mem`, or an unverified contributor figure. Bytes are converted to
GiB using 2^30; decimal GB uses 10^9. See the
[Linux proc documentation](https://docs.kernel.org/filesystems/proc.html).

Three fresh-host trials characterize this corpus only. They do not establish a
worst-case bound for all source files or policies allowed by k <= 8: band counts,
operation lists, imported environments and proof terms can change memory needs.
