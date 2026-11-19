# D-002: Temperature input

Status: proposed

Covers target-table row 4 (temperature compensation).

## Context

A toaster that is already hot browns faster; the chip must shorten the
timing when the interior is preheated. The pre-ratification table fixed the
shape — time shortened from an external NTC input, slope set by one
resistor — but not how one resistor sets the slope while the NTC and the
resistor share a pin budget the 8-pin target (row 12) already fully spends.

## Options considered

1. **Dedicated compensation pin pair** (NTC and slope resistor each on a
   pin). Spends two pins; the 8-pin target cannot afford it (D-006).
2. **Divider on one pin**: the NTC goes from VDD to the NTC pin, the slope
   resistor goes from the NTC pin to GND; the pin voltage is the divider
   tap. The slope resistor's value sets the divider ratio — i.e. how
   strongly the NTC's resistance change moves the tap, which is the
   compensation strength — with one resistor on one pin.
3. **On-chip temperature sensor** (parasitic-device based). Measures the
   chip, not the toaster cavity; the preheated-cavity information is lost.

## Decision (kept, wiring made explicit)

Option 2. NTC pin 4: an NTC of the 10 kΩ-at-25 °C class from VDD to pin 4,
and a board slope resistor from pin 4 to GND. An internal window comparator
reads the tap. The 10 kΩ-at-25 °C class is a common appliance-board
convention carried into this spec, not a value cited to a fetched primary
source; the datasheet-to-be names a specific part. Row 4 kept: compensation
only shortens — 0–50 % of the selected time at target, 0–25 % fallback —
never extends. The slope resistor is chosen at board build time by the
selection rule below; the chip guarantees the transfer is bounded and
monotonic, so the same board part always yields the same compensation.

Fault semantics (row 8): an open NTC leaves the tap pulled to GND by the
slope resistor; a shorted NTC pulls the tap to VDD. Both fall outside the
comparator window (edges at ≈ 15 % and ≈ 85 % of VDD); compensation is
disabled and the selected time stands, still bounded by the max-time limit
(row 9, D-004).

Window-vs-cavity reconciliation: a hotter cavity lowers the NTC's
resistance and raises the tap, so the hot end of the compensation span
pushes toward the ≈ 85 % shorted-sensor edge — in exactly the condition
compensation exists to serve. The board therefore selects the slope
resistor by the divider rule: with tap fraction f = R_slope / (R_NTC +
R_slope) and the declared cavity span from its coldest compensated
temperature to its hottest, choosing
0.18 × R_NTC(cold end) < R_slope < 5.6 × R_NTC(hot end) keeps the tap
inside the window (0.15 < f < 0.85) at both ends of the span. Boards pick
inside those bounds for margin at both edges; the hot end is the binding
edge, since heating only raises the tap. The chip guarantees the window
edges and publishes this selection rule (repeated in the datasheet-to-be);
the ngspice sweep verifies the declared span's hot end stays clear of the
85 % edge.

## Rejected alternatives

- Option 1: two pins is outside the 8-pin budget; the slope resistor is a
  calibration, not an interface — it does not need its own pin.
- Option 3: measures the wrong temperature; a chip-level sensor cannot see a
  preheated toaster cavity.

## Consistency with other groups

- Row 9 (D-004): a compensation fault can only fall back to the selected
  time, never extend it, so the max-time limit is unaffected.
- Row 12 (D-006): one pin for the whole feature; the pin list holds.

## Sources

- PT8A2514A datasheet,
  <https://www.diodes.com/assets/Datasheets/PT8A2514A.pdf> (accessed
  2026-11-19): its typical application circuit includes an external NTC
  (NTC1 with R11 = 27 kΩ, marked as adjusted) in the resistor network
  feeding the oscillator — circuit-level evidence that a board NTC carries
  the cavity-temperature information into the timing path (D-007); it is
  not a divider-tap precedent for option 2's window-comparator readout.

## Verification

NTC behavioural model (beta-parameter) swept in ngspice across the `klt sim`
corner grid: tap voltage vs. cavity-temperature proxy, window edges,
compensation span and monotonicity; hot-end margin to the ≈ 85 % edge for
the declared cavity span under the slope-resistor selection rule. Fault
injection (open/short) in ngspice for the window edges, and in cocotb for
the fallback-to-selected-time behaviour and the span bounds.
