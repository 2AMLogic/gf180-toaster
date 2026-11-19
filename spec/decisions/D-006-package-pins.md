# D-006: Package and pins

Status: proposed

Covers target-table row 12 (pin count) and the pin list.

## Context

The pre-ratification table fixed ≤ 8 signal and supply pins but no pin list.
Every other group had to fit behind those 8 pins: the RSET interface
(D-001), the NTC divider (D-002), the driver (D-003), the lever/cancel
(D-004), power (D-005) and test access.

## Options considered

1. **8 pins, DIP-8 compatible** — the comparison part's package (lead-free
   DIP-8), also footprint-compatible with SOIC-class 8-pin housings.
2. **10–14 pins.** Room for a separate cancel pin, a slope-resistor pin, a
   button matrix — and outside the pre-ratification bound and the cost class
   the comparison part defined.
3. **5 pins** — the comparison family's PT8A2515 ships TO-94 and SOT23-5
   packages. Possible for a knobless timer variant; not enough for
   RSET + NTC + lever + driver + power with test access.

## Decision (kept, pin list added)

Row 12 kept: ≤ 8 signal and supply pins. Fallback: 8 pins with TEST
power-on-strapped to LEVER_N (one pad recovered). Stretch: 6 pins (TEST
strap removed, NC pin dropped) — credible because the comparison family
ships a 5-pin timer variant.

Pin list (source of truth for the bondpad map and LVS):

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

Supplies sit at the package ends (pins 1 and 8) — this spec's own choice,
for a clean bondpad map and a symmetric pinout. No convention precedent is
claimed: the comparison part assigns its supplies differently (VCC at pin
4, GND at pin 8), so the pin choice here is justified by layout
convenience, not by the incumbent's example.

## Rejected alternatives

- Option 2: outside the pre-ratification bound and the commercial cost class.
- Option 3 as the target: drops test access or the NTC feature.

## Consistency with other groups

- Every interface decision cites this record; the count closes at 8 with one
  NC pin spare, which is also the recovery path if a later decision needs a
  ninth function — it must come back here first.

## Sources

- PT8A2514A datasheet,
  <https://www.diodes.com/assets/Datasheets/PT8A2514A.pdf> (accessed
  2026-11-19): lead-free DIP-8 package; full pin configuration and
  description table (8 pins: Defrost, Reheat, Bagel, VCC, RX, OSC, CX,
  GND).
- PT8A2515 datasheet,
  <https://www.diodes.com/assets/Datasheets/PT8A2515.pdf> (accessed
  2026-11-19): TO-94 and SOT23-5 packages — the family's 5-pin timer
  variant, the credibility anchor for the 6-pin stretch.

## Verification

Bondpad/pin map in LVS via `klt` (names, directions, count); pin-map
behaviour in cocotb (pull-ups present, test entry only via TEST).
