# Local validation and hosted judging boundary

The evaluator runs **trusted local solutions**. The command timeout, stripped environment and
POSIX process-group cleanup are not a hostile-code sandbox. A solution can still read/write files
with the current user's permissions, use the network, or consume disk/memory. Do not run external
submissions on a workstation or in a process carrying private data.

Before hosted judging: use a separately provisioned isolated runner, read-only original model,
no credentials/network, CPU/memory/disk/output quotas, process-tree cancellation, and a fresh job
filesystem. Validate a child-process timeout, file-write attempt and network denial in that runner.
That acceptance requires the actual hosting environment; no deployment is established here.

## Local contracts

- Model/solution trees cannot overlap evaluator work/output paths.
- Explicit restored/archive output paths must be new; existing content is preserved. Default
  restoration uses a unique evaluator-owned directory and remains available for inspection.
- Every command has `--command-timeout-seconds` (default 3600); nonpositive values fail early.
- `score_spec_version=2.0-structural-gates` requires original paths, root DATA files and the INCLUDE
  graph to be preserved. Missing mandatory structure yields invalid status/exit 1, even if other
  score components are positive. This is not a physical-equivalence or float-accuracy threshold.
- Baseline archives reject conflicting file/directory paths before writes and are bounded to
  100,000 entries / 100 GiB expanded bytes. The extraction function accepts smaller explicit
  budgets for callers/tests. These defaults are local safety limits, not competition rankings.
- Gzip/tar metadata is normalized, so equal input file paths/bytes give equal archive bytes
  within the same Python/zlib runtime. Historical leaderboard rows are not rewritten.

## Validation

```bash
python3 -m pytest baseline/tests public_evaluator/tests -q
```

## tNavigator acceptance (requires licensed software and authorized fixtures)

Record simulator version/platform and source-fixture SHA-256. Run one known-good deck, one
missing-INCLUDE deck and one physically altered deck, keeping simulator status/logs separately
from structural and tolerant-content metrics. Repeat after codec changes. A successful tar
round-trip alone does not qualify physical reservoir simulation equivalence or private judging.

Unacknowledged script execution is rejected before launching a process. For reviewed trusted
local code only, pass `--allow-trusted-local-execution`; the low-level Python runner similarly
requires `trusted=True`. This acknowledgement grants host-user privileges and does not sandbox
a submission. Artifact-only checking requires neither execution acknowledgement nor a simulator.
