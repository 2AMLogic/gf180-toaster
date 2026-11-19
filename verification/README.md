# verification

cocotb testbenches. Recorded results are append-only evidence.

`smoke/` is the digital harness bring-up (issue #2): a trivial
`smoke_counter.v` stand-in — not the Toaster timer — plus one cocotb test,
run by the repo-root `make smoke` (cocotb + Icarus; see `smoke/Makefile`).
Each run appends a JSON evidence record under `smoke/evidence/`.
