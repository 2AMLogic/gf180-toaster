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

## Target specification (DRAFT — engineering to ratify, see issue #1)

| Parameter | Target | Stretch |
|---|---|---|
| Supply voltage | 5 V ± 10 %, from an external low-voltage supply |  |
| Browning time range | 20 s to 240 s, set by one external resistor or potentiometer | 10 s to 300 s |
| Timing accuracy | ± 5 % over −10 °C to 85 °C and supply range | ± 2 % |
| Temperature compensation | time shortened from an external NTC input; slope set by one resistor |  |
| Hold-down output | low-side driver, ≥ 50 mA sink, for a holding solenoid or an external transistor | ≥ 150 mA |
| Cancel input | releases the hold-down within 50 ms | 10 ms |
| Standby current | ≤ 100 µA with the lever up | ≤ 20 µA |
| Pin count | ≤ 8 signal and supply pins |  |
| AREA | ≤ 0.25 mm² core | ≤ 0.10 mm² |

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
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
