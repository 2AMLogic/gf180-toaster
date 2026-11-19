# gf180-toaster

A Toaster controller on the [gf180mcu](https://github.com/google/gf180mcu-pdk) open PDK,
designed by AI agents driving
[klayout-tools](https://github.com/2AMLogic/klayout-tools) and the open-source
flow — cocotb + Icarus and Yosys/OpenROAD on the digital side, xschem + ngspice
on the analog side.

It is the whole control side of a pop-up toaster in one chip: set the browning level, push the lever down, and the chip holds the carriage for the right time, shortens that time when the toaster is already hot, and lets go.

**Where the idea came from.** A toaster is the plainest possible demonstration of open silicon: everyone owns one, the function fits in a sentence, and the result can be put on a kitchen counter. The prompt was a post on r/nicechips, a forum where engineers share parts they admire: [a toaster controller in a DIP8, "all your toaster timing needs"](https://www.reddit.com/r/nicechips/comments/mor0ze/diodespericom_pt8a2514a_toaster_controller_ic_all/). This repo is an original design of the same kind of part, not a copy of the one in the post.

## Status

```
Area (measured, post-layout): not yet measured
Design stage: spec
```

<!-- The two fields above are correct as they stand for a repo where nothing is
     built yet, so there is nothing to fill in here — only to keep current. They
     are a fixed-location summary a chip-level integrator reads without parsing
     the prose below, so the keys, their spelling, and their order do not change.

     Area (measured, post-layout): the as-built area of a DRC-clean,
     LVS-matched layout, as a number with units (e.g. 1.059 mm²), taken from
     this repo's own committed measurement. Never a target, a plan, or an
     estimate — the ratified bound is the AREA row of the target-specification
     table below. Until a layout exists, the literal "not yet measured".

     Design stage: exactly one token, from this ladder, in order —
     spec | schematic-sim | layout-DRC-LVS-clean | extracted-verified | silicon
     — describing what this repo has produced and recorded evidence for, never
     what it is aiming at. One token; not a range, not a hedge, not prose. -->

**Just opened, specification phase.** Nothing is designed yet, nothing has been
taped out, and nothing has been measured.

Not being built here: anything that touches mains voltage. The low-voltage supply, the heating elements, the solenoid or its power transistor, and every safety cut-out are outside the chip and outside this repo. Extra modes (defrost, reheat, bagel) wait until the basic timer is verified. No schematic, layout or silicon exists yet.

## Built agent-native

Every specification, decision record, testbench, and line of documentation in
this repo is produced by AI agents working from a ratified spec and an
append-only evidence trail — not human-authored work that agents merely
assisted with. Verification is the product: every claim traces to a recorded
result. Where the agents hit friction with the open-source tooling — most often
[klayout-tools](https://github.com/2AMLogic/klayout-tools) — that friction gets
filed as a public issue against the tool itself, so the fix benefits everyone
using gf180mcu, not just this repo.

## Target specification

The target specification and its decision records live in
[`spec/README.md`](spec/README.md) — that file is the source of truth (its
`Status:` line records ratification); the table below mirrors its
target/fallback/stretch values. Per-row verification methods are in the spec.

| Parameter | Target | Fallback | Stretch |
|---|---|---|---|
| Supply voltage | 5 V ± 10 % (4.5 V–5.5 V), external low-voltage supply | 5 V ± 5 % (regulated rail assumed) | 3.0 V–5.5 V single rail |
| Browning time range | 20 s–240 s in 20 s steps (12 levels), set by one external resistor or potentiometer on RSET (47 kΩ–470 kΩ) | 40 s–180 s in 20 s steps (8 levels), same RSET interface | 10 s–300 s in 10 s steps (30 levels) |
| Timing accuracy | ± 5 % of selected time over −10 °C to +85 °C and the target supply range | ± 10 % under the same conditions | ± 2 % |
| Temperature compensation | Time shortened from an external NTC (10 kΩ at 25 °C class) with the compensation strength set by one board resistor; reduction span 0–50 % of selected time; compensation never extends the time | Reduction span 0–25 % | Proportional compensation, no quantization |
| Hold-down output | Low-side driver sinking ≥ 50 mA at VOL ≤ 0.6 V across corners, for a holding solenoid or an external transistor; external coil clamp mandatory | ≥ 30 mA sink; external transistor mandatory above that | ≥ 150 mA sink |
| Cancel input | Hold released within 50 ms of the cancel condition asserting (measured to zero coil current) | Within 100 ms | Within 10 ms |
| Power-on state | Hold-down released from POR release until a valid start (lever down, browning level decoded) | — | — |
| NTC fault behaviour | Open or shorted NTC disables compensation; the selected time stands; the max-time limit still applies | — | Fault class readable on TEST |
| Max-time limit | Any hold ends by 1.25 × the maximum settable time (300 s at target range) regardless of timing-core state | Fixed 300 s absolute ceiling | Ceiling follows the stretch range (375 s) |
| Supply-loss behaviour | Hold released when VDD falls below the brown-out threshold (≈ 2.7 V falling, hysteresis ≥ 150 mV); released until a valid start | — | — |
| Standby current | ≤ 100 µA with the lever up (oscillator gated, driver off) | ≤ 250 µA | ≤ 20 µA |
| Pin count | ≤ 8 signal and supply pins, DIP-8-compatible pin list in this spec | 8 pins, TEST power-on-strapped to LEVER_N | 6 pins (TEST strap removed, NC dropped) |
| AREA | ≤ 0.25 mm² core | ≤ 0.50 mm² core | ≤ 0.10 mm² core |

## Repo layout

```
design/        schematics (xschem)
flow/          synthesis + place-and-route (Yosys, OpenROAD)
layout/        GDS + DRC/LVS reports (klayout-tools driven)
measurements/  silicon characterization (empty until tape-out)
rtl/           Verilog sources
sim/           analog testbenches + PVT corner results
spec/          ratified spec + decision records
verification/  cocotb testbenches
WORK_LOG.md    merged PRs / closed issues (maintained by Guide)
WORK_PLAN.md   backlog snapshot from label state (maintained by Guide)
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
