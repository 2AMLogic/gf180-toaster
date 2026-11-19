# Target specification

Status: proposed

Ratification path: this specification and the seven decision records under
`spec/decisions/` are ratified when the pull request carrying them is
reviewed and merged after two independent reviews (engineering and market)
by sessions that did not author them — at which point every `Status:` line
in this directory is flipped `proposed` → `RATIFIED` inside that same pull
request, never as a follow-up commit. Until then the values below are the
working target table; agents do not relax a ratified value to make a result
pass (CLAUDE.md).

Written 2026-11-19 from the pre-ratification README table (issue #1). Each
decision record states whether the pre-ratification value was kept or
changed, with evidence.

## Scope

One chip: the control side of a pop-up toaster — browning level set by an
external resistor, lever-latched hold-down, time shortened when the toaster
is already hot, cancel, and nothing else. Anything touching mains voltage is
outside the chip and outside this repo; the solenoid is an external
low-voltage load. Extra modes (defrost, reheat, bagel) and a microcontroller
are excluded until the basic timer, the temperature input and the hold-down
driver are verified (CLAUDE.md).

## Target table

Source of truth for every parameter. "Fallback" is the relaxed bound the
design ships with if the target proves unachievable; "Stretch" is attempted
only after the target is verified. A row marked "—" is a safety or interface
invariant with a single required value.

| # | Parameter | Target | Fallback | Stretch | Verification method |
|---|-----------|--------|----------|---------|---------------------|
| 1 | Supply voltage | 5 V ± 10 % (4.5 V–5.5 V), external low-voltage supply | 5 V ± 5 % (regulated rail assumed) | 3.0 V–5.5 V single rail | ngspice supply grid via `klt sim`; supply-loss/BOR behaviour in cocotb |
| 2 | Browning time range | 20 s–240 s in 20 s steps (12 levels), set by one external resistor or potentiometer on RSET (47 kΩ–470 kΩ) | 40 s–180 s in 20 s steps (8 levels), same RSET interface | 10 s–300 s in 10 s steps (30 levels) | RSET decode + counter chain in cocotb; RSET transfer (resistance → level) in ngspice across the `klt sim` corner grid |
| 3 | Timing accuracy | ± 5 % of selected time over −10 °C to +85 °C and the target supply range | ± 10 % under the same conditions | ± 2 % | oscillator period across the `klt sim` corner grid + Monte Carlo (ngspice); end-to-end time in cocotb |
| 4 | Temperature compensation | Time shortened from an external NTC (10 kΩ at 25 °C class) with the compensation strength set by one board resistor; reduction span 0–50 % of selected time; compensation never extends the time | Reduction span 0–25 % | Proportional compensation, no quantization | NTC behavioural model swept in ngspice across corners (`klt sim`); scaling and span bounds in cocotb |
| 5 | Hold-down output | Low-side driver sinking ≥ 50 mA at VOL ≤ 0.6 V across corners, for a holding solenoid or an external transistor; external coil clamp mandatory | ≥ 30 mA sink; external transistor mandatory above that | ≥ 150 mA sink | Driver characterization in ngspice across corners (`klt sim`); device rating and clamp tolerance in DRC/LVS/EMIR via `klt` |
| 6 | Cancel input | Hold released within 50 ms of the cancel condition asserting (measured to zero coil current) | Within 100 ms | Within 10 ms | Reaction bound in cocotb; analog release delay in ngspice |
| 7 | Power-on state | Hold-down released from POR release until a valid start (lever down, browning level decoded) | — | — | POR sequence in cocotb; POR threshold in ngspice (`klt sim`) |
| 8 | NTC fault behaviour | Open or shorted NTC disables compensation; the selected time stands; the max-time limit still applies | — | Fault class readable on TEST | Fault injection in cocotb; window-comparator thresholds in ngspice |
| 9 | Max-time limit | Any hold ends by 1.25 × the maximum settable time (300 s at target range) regardless of timing-core state | Fixed 300 s absolute ceiling | Ceiling follows the stretch range (375 s) | Randomized runs + counter-corruption and oscillator-stall injection in cocotb |
| 10 | Supply-loss behaviour | Hold released when VDD falls below the brown-out threshold (≈ 2.7 V falling, hysteresis ≥ 150 mV); released until a valid start | — | — | BOR threshold/hysteresis sweep in ngspice (`klt sim`); release and stay-released sequence in cocotb |
| 11 | Standby current | ≤ 100 µA with the lever up (oscillator gated, driver off) | ≤ 250 µA | ≤ 20 µA | IDD across the `klt sim` corner grid (ngspice) |
| 12 | Pin count | ≤ 8 signal and supply pins, DIP-8-compatible pin list in this spec | 8 pins, TEST power-on-strapped to LEVER_N | 6 pins (TEST strap removed, NC dropped) | Bondpad/pin map in LVS via `klt`; pin-map behaviour in cocotb |
| 13 | AREA | ≤ 0.25 mm² core | ≤ 0.50 mm² core | ≤ 0.10 mm² core | Post-place-and-route area report via `klt` |

## Pin list (8-pin package)

| Pin | Name | Dir | Function |
|-----|------|-----|----------|
| 1 | GND | P | Ground |
| 2 | DRV | O | Hold-down low-side driver (solenoid or external transistor return); external coil clamp is a board requirement |
| 3 | RSET | I | Browning set resistor to GND, 47 kΩ–470 kΩ (D-001) |
| 4 | NTC | I | NTC from VDD to this pin; slope resistor from this pin to GND (D-002) |
| 5 | LEVER_N | I | Lever / cancel input, internal pull-up, active low (D-004) |
| 6 | TEST | I/O | Factory test entry; strap low or leave floating in the application |
| 7 | NC | — | Reserved, no internal connection |
| 8 | VDD | P | Supply, 4.5 V–5.5 V (D-005) |

## Cross-group reconciliations

- **Standby vs. oscillator choice**: the timing oscillator is gated off with
  the lever up; standby powers only the POR/BOR supervisor and the lever
  monitor, which is what makes ≤ 100 µA (and the 20 µA stretch) reachable on
  this PDK.
- **Timing accuracy vs. compensation**: rows 3 and 4 are independent budgets
  — row 3 bounds the base time, row 4 may only multiply it downward (never
  extend), so compensation error cannot violate the max-time limit (row 9).
- **Supply range vs. driver**: the driver uses the PDK's 6 V device class
  (confirmed: the gf180mcu spice model family is a "0.18um 3.3v/6v High
  Voltage MCU Process"), so a 5.5 V rail top plus coil-clamp transients stay
  inside the device rating by construction.
- **Pin budget vs. compensation and cancel**: compensation needs exactly one
  pin (NTC; the slope resistor shares that pin to GND, D-002) and cancel
  shares LEVER_N (D-004); this is what keeps rows 4 and 6 inside the 8-pin
  target of row 12.

## Verification harness mapping

Every verification method above names a concrete testbench class that the
verification harness (issue #2) hosts: cocotb testbenches (Icarus), ngspice
corner grids and Monte Carlo runs through `klt sim`, and layout checks
(DRC, LVS, EMIR, area) through `klt`.

## Decision records

| Record | Parameter group | vs. pre-ratification table |
|--------|-----------------|----------------------------|
| [D-001](decisions/D-001-timing-core.md) | Timing core (rows 2, 3) | kept, interface made explicit |
| [D-002](decisions/D-002-temperature-input.md) | Temperature input (row 4) | kept, wiring made explicit |
| [D-003](decisions/D-003-hold-down-driver.md) | Hold-down driver (row 5) | kept, bounds made explicit |
| [D-004](decisions/D-004-cancel-and-fault.md) | Cancel / fault (rows 6, 7, 8, 9, 10) | kept, safety semantics added |
| [D-005](decisions/D-005-supply.md) | Supply (rows 1, 10, 11) | kept, BOR added |
| [D-006](decisions/D-006-package-pins.md) | Package / pins (row 12) | kept, pin list added |
| [D-007](decisions/D-007-comparison-basis.md) | Comparison basis | new |

## Sources

Primary sources cited by the decision records (all accessed 2026-11-19):

- PT8A2514A datasheet — Diodes Inc., document 12-03-0003 (PT0271-2,
  2012-03-28): <https://www.diodes.com/assets/Datasheets/PT8A2514A.pdf>
- PT8A2515 datasheet — Diodes Inc., document 2013-07-0010 (PT0272-3,
  2013-08-01): <https://www.diodes.com/assets/Datasheets/PT8A2515.pdf>
- gf180mcu PDK documentation: <https://gf180mcu-pdk.readthedocs.io/en/latest/>
  (analog device-model page:
  <https://gf180mcu-pdk.readthedocs.io/en/latest/analog/Analog.html>)
- gf180mcu PDK repository: <https://github.com/google/gf180mcu-pdk>
- r/nicechips thread on the PT8A2514A (provenance of the idea, linked from
  the README):
  <https://www.reddit.com/r/nicechips/comments/mor0ze/diodespericom_pt8a2514a_toaster_controller_ic_all/>
