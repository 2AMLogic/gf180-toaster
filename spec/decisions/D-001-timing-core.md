# D-001: Timing core

Status: proposed

Covers target-table rows 2 (browning time range) and 3 (timing accuracy) and
the RSET interface.

## Context

The browning time is a count of clock periods: time = N / f. Two things must
be set: how long (the browning level, chosen by a person at the knob) and how
fast the chip counts (a time base whose accuracy bounds row 3). The
pre-ratification README table fixed the bounds — 20 s to 240 s from one
external resistor or potentiometer, ± 5 % accuracy over temperature and
supply, ± 2 % stretch — but not the interface or the architecture.

## Options considered

1. **External R + external C oscillator** (the PT8A2514A approach: RX and CX
   pins, ≈ 8.192 kHz at R1 = 47 kΩ with C1 marked 332 — EIA code 3.3 nF —
   per its test circuit). Familiar, but spends a second pin on the capacitor
   and puts part of the accuracy budget in a cheap board capacitor.
2. **External R only, internal capacitor** — the resistor programs an
   internal, supply-referenced bias current that charges an internal PDK
   capacitor; an internal comparator closes the relaxation loop. One pin
   spent; the capacitor is a known, PDK-documented device.
3. **Fully internal oscillator; the resistor only selects the count target.**
   One pin, but the oscillator is internal either way, so this buys nothing
   over option 2 while making the RSET transfer less direct.
4. **External clock input.** No toaster board carries a clock source.

## Decision (kept, made explicit)

Option 2. RSET (pin 3) takes the set resistor to GND, 47 kΩ–470 kΩ; an
internal supply-referenced bias current turns its value into a monotonic
level select. The transfer is quantized to 12 levels of 20 s spanning
20 s–240 s (target); a potentiometer parks between levels and decode
hysteresis picks the nearest.

Row 2 kept: 20 s–240 s target (12 levels), 40 s–180 s fallback (8 levels,
same interface), 10 s–300 s stretch (30 levels of 10 s). Row 3 kept: ± 5 %
target, ± 10 % fallback, ± 2 % stretch, over −10 °C to +85 °C and the target
supply range.

Rationale for the quantized mapping: a toaster knob has detents or a
continuous pot, and a 20 s granularity over 20–240 s sits beside the
commercial comparison part's published band (7808–8576 Hz at a fixed R/C is
± 4.7 %) — comparable to our row 3 target, over a narrower temperature range
(0–85 °C vs. our −10 °C to +85 °C).

The accuracy budget excludes the external resistor's own tolerance and
tempco (verification uses a 0.1 % test resistor); the chip's contribution is
the bias-current and capacitor drift plus comparator offset, which the
corner grid and Monte Carlo measure directly.

## Rejected alternatives

- Option 1 (external R + C): spends a pin the 8-pin budget needs (D-006) and
  moves accuracy into an uncontrolled board part. The PT8A2514A can afford
  RX and CX — its relay/key architecture does not carry our NTC and driver
  pins.
- Option 3: no benefit over option 2.
- Option 4: no clock source exists at the board level this chip targets.

## Consistency with other groups

- Standby (row 11, D-005): the oscillator and its RSET bias are gated off
  with the lever up.
- Compensation (row 4, D-002) multiplies the selected time downward only, so
  the accuracy budget of row 3 is never consumed by compensation.
- Max-time limit (row 9, D-004): 1.25 × 240 s = 300 s — the ceiling is
  consistent with the target range by construction.

## Sources

- PT8A2514A datasheet, Diodes Inc.,
  <https://www.diodes.com/assets/Datasheets/PT8A2514A.pdf> (accessed
  2026-11-19): external R/C oscillator (≈ 8.192 kHz at R1 = 47 kΩ), published
  frequency band 7808–8576 Hz, adjustable timer 30 s–10 min, operating
  temperature 0–85 °C.
- gf180mcu PDK,
  <https://gf180mcu-pdk.readthedocs.io/en/latest/analog/Analog.html>
  (accessed 2026-11-19): 0.18 µm 3.3v/6v high-voltage MCU process spice
  models — the device families the internal capacitor and bias network are
  drawn from.

## Verification

Row 2: RSET decode and counter-chain tests in cocotb (level selection,
hysteresis, 12-level mapping); RSET transfer from resistance to level in
ngspice across the `klt sim` corner grid. Row 3: oscillator period across
the `klt sim` corner grid (5 MOS corners × supply × temperature) and Monte
Carlo; end-to-end selected time in cocotb.
