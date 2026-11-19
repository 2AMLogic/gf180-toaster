# gf180-toaster -- one-command verification entry points.
#
#   make smoke   Verification-harness smoke, the one command a clean checkout
#                needs: the analog ngspice smoke experiment (single typical
#                corner, appends an evidence record under sim/smoke-op/) and
#                the digital cocotb + Icarus smoke (appends a JSON evidence
#                record under verification/smoke/evidence/). Needs ngspice,
#                iverilog, the gf180mcu PDK and cocotb on PATH (see
#                sim/harness/README.md and verification/smoke/). Minutes.
#
# All targets exit non-zero on any failure.

SMOKE_SUBSET_REASON ?= smoke: single typical corner by design -- harness bring-up, not block evidence (issue 2)

.PHONY: help smoke smoke-analog smoke-digital

help:
	@echo "make smoke   analog ngspice smoke + digital cocotb smoke; appends evidence records"

smoke: smoke-analog smoke-digital

smoke-analog:
	cd sim && python3 -m harness.cli run smoke-op \
		--subset-reason "$(SMOKE_SUBSET_REASON)"

smoke-digital:
	$(MAKE) -C verification/smoke
	python3 verification/smoke/record_evidence.py
