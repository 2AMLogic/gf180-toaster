# Work Log

Chronological record of merged PRs and closed issues, maintained by the Guide role.

## Archives

_None yet._

## Entries

### 2026-11-28

- **PR #28**: fix: wire npm test and check scripts to the Make verification targets
- **Issue #27** (closed): Replace always-passing npm verification scripts with the existing Make targets

### 2026-11-23

- **PR #22**: feat(layout): enforce the pinned open_pdks revision in the DRC smoke
- **Issue #20** (closed): Layout smoke records PDK provenance but does not enforce it (and hides klt's ambiguous_sources)
- **PR #19**: feat(layout): pin klt 0.9.2 and add the layout DRC smoke with append-only evidence
- **Issue #16** (closed): Bring up the layout harness: pin klt in layout/toolchain.json and record a DRC smoke run

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
