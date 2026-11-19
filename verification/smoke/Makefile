# cocotb + Icarus smoke runner for verification/smoke.
#
# Invoked by the repo-root Makefile's smoke-digital target; usable
# stand-alone from this directory (needs cocotb on PATH, i.e. an activated
# venv with cocotb installed, plus iverilog). Writes results.xml in this
# directory; record_evidence.py turns that into the append-only JSON
# evidence record the repo-root `make smoke` commits/uploads.

TOPLEVEL_LANG = verilog
SIM = icarus
VERILOG_SOURCES = rtl/smoke_counter.v
TOPLEVEL = smoke_counter
MODULE = test_smoke_counter
SIM_BUILD = build

include $(shell cocotb-config --makefiles)/Makefile.sim
