# sim

Testbenches and PVT corner results (ngspice). `harness/` carries the generic
sim-harness-core scaffold (see `harness/README.md`, including its
generic-vs-block-specific split). The block-authored config
(`sim/pdk.json`, `sim/toolchain.json`) and the first experiment
(`sim/smoke-op/`, run by the repo-root `make smoke`) are the harness
bring-up from issue #2; testbenches and results for real block content
arrive with the first design issue.
