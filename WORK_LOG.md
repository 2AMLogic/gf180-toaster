# Work Log

Chronological record of merged PRs and closed issues, maintained by the Guide role.

## Archives

_None yet._

### 2026-11-22

- **PR #17**: chore(loom): resync installed Loom to v0.20.8 (clears the dispatch hold)

### 2026-11-19

- **PR #15**: docs: list Makefile in README repo layout
- **PR #14**: test: fail make test-unit when a tracked stdlib test_*.py is not in UNIT_TESTS
- **Issue #13** (closed): README Repo layout listing omits Makefile (the smoke/unit gate entry point)
- **Issue #11** (closed): make test-unit's explicit file list can silently fall behind: nothing checks that a new stdlib unittest is wired to the gate
- **PR #10**: ci: gate the record_evidence unit tests via make test-unit
- **PR #8**: fix: record failing digital smoke runs before exiting non-zero
- **PR #5**: feat: bootstrap verification harness -- ngspice + cocotb smoke, CI, evidence
- **Issue #9** (closed): test_record_evidence.py is not run by any gate (make smoke / CI never invoke it)
- **Issue #6** (closed): Digital smoke writes no evidence record for a failing run (analog half does)
- **Issue #2** (closed): Bootstrap the verification harness
