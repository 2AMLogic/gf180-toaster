# gf180-toaster -- one-command verification entry points.
#
#   make test-unit  Stdlib unit tests, by explicit file path -- never
#                   `unittest discover`, which would also collect the cocotb
#                   test module. The explicit UNIT_TESTS list is checked for
#                   completeness first (a tracked stdlib test_*.py that the
#                   list misses fails the target, issue #11). Needs only
#                   python3; no ngspice, iverilog, cocotb or PDK.
#
#   make smoke   Verification-harness smoke, the one command a clean checkout
#                needs: the analog ngspice smoke experiment (single typical
#                corner, appends an evidence record under sim/smoke-op/),
#                the digital cocotb + Icarus smoke (appends a JSON evidence
#                record under verification/smoke/evidence/), and the layout
#                DRC smoke (placeholder cell through the pinned klt, appends
#                an evidence record under layout/records/). Needs ngspice,
#                iverilog, the gf180mcu PDK, cocotb and klt on PATH (see
#                sim/harness/README.md, verification/smoke/ and
#                layout/README.md). Minutes.
#
# All targets exit non-zero on any failure.

SMOKE_SUBSET_REASON ?= smoke: single typical corner by design -- harness bring-up, not block evidence (issue 2)

# klt for the layout smoke: first on PATH (CI: the pinned install in the
# project venv). Point KLT at a throwaway pinned install to run it locally
# without touching the host tool, e.g.
#   make smoke-layout KLT=/tmp/klt-pin/bin/klt
KLT ?= klt

.PHONY: help test-unit smoke smoke-analog smoke-digital smoke-layout

help:
	@echo "make test-unit  stdlib unit tests only (needs just python3)"
	@echo "make smoke   analog ngspice + digital cocotb + layout klt smokes; appends evidence records"

# The one list both the runner (below) and the completeness check read --
# a second copy would be the very drift the check exists to catch.
# `unittest discover` stays rejected: it would import the cocotb test
# module outside the cocotb runner (issue #9).
UNIT_TESTS = \
	verification/smoke/test_check_unit_list.py \
	verification/smoke/test_record_evidence.py \
	layout/test_toolchain_pin.py \
	.github/test_ci_toolchain_pins.py

test-unit:
	python3 verification/smoke/check_unit_list.py $(UNIT_TESTS)
	@for t in $(UNIT_TESTS); do \
		echo "== $$t"; \
		python3 "$$t" || exit 1; \
	done

smoke: smoke-analog smoke-digital smoke-layout

smoke-analog:
	cd sim && python3 -m harness.cli run smoke-op \
		--subset-reason "$(SMOKE_SUBSET_REASON)"

smoke-digital:
	$(MAKE) -C verification/smoke
	python3 verification/smoke/record_evidence.py

smoke-layout:
	python3 layout/run_smoke.py --klt "$(KLT)"
