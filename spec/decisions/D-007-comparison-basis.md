# D-007: Comparison basis

Status: proposed

New record — the pre-ratification README table had no comparison basis.
The target-spec issue (#1) asks for commercial toaster-timer parts from
current public datasheets, with the PT8A2514A family (the README's starting
point) listed first.

## Context

This part re-creates a category that exists commercially — the single-chip
toaster controller — and the market review needs to read, from primary
sources, what the incumbent parts actually do, so the target table can be
judged as competitive rather than invented. This record fixes what the
public datasheets say and where this specification deliberately departs.

## The parts

**PT8A2514A** — Diodes Inc. (Pericom lineage), "New Toaster Controller",
document 12-03-0003 (PT0271-2, 2012-03-28), lead-free DIP-8:

| Aspect | PT8A2514A (published) | gf180-toaster (this spec) |
|--------|------------------------|---------------------------|
| User interface | Button-driven: Defrost / Reheat / Bagel keys, key-scan circuit | Knob (RSET resistor) + mechanical lever latch |
| Load switch | Relay through an external 8050 NPN with a 1N4148 clamp; pins ≤ 20 mA absolute-maximum, LED-drive class | On-chip low-side driver, ≥ 50 mA at VOL ≤ 0.6 V (stretch ≥ 150 mA) |
| Time setting | External R/C oscillator (RX, CX pins), ≈ 8.192 kHz at 47 kΩ / 3.3 nF, adjustable 30 s–10 min | One external resistor (RSET), internal capacitor; 20–240 s in 20 s steps |
| Accuracy | Published band 7808–8576 Hz (± 4.7 %) at fixed R/C, 0–85 °C | ± 5 % over −10 °C to 85 °C and supply range |
| Temperature input | External NTC in the application circuit's resistor network (NTC1, R11 = 27 kΩ); effect documented at circuit level only | Ratified behaviour: 0–50 % reduction, slope resistor, open/short fault semantics |
| Safety | POR clears outputs; relay output disabled if the external oscillator is wrong | POR hold-release; BOR release; max-time ceiling; NTC-fault fallback |
| Supply | 3.5–5.5 V, ICC ≤ 500 µA running | 4.5–5.5 V, ≤ 100 µA standby |
| Modes | Defrost / Reheat / Bagel | None — excluded until the basic timer, temperature input and hold-down driver are verified (CLAUDE.md) |

**PT8A2515** — same family, "Simple Timer", document 2013-07-0010
(PT0272-3, 2013-08-01): 3.5–5.5 V, external R/C adjustable 30 s–20 min,
TO-94 and SOT23-5 packages. Cited as the family's low-pin-count anchor for
the 6-pin stretch (D-006).

## What this comparison establishes

- The category, the 8-pin cost class and the ~5 V rail are commercial
  reality, not aspirations (PT8A2514A).
- The incumbent's load-switch path is external-transistor based; an on-chip
  ≥ 50 mA low-side sink is the differentiator row 5 claims — and the PDK's
  6 V device class is what makes it claimable (D-003).
- The incumbent's accuracy figure (± 4.7 % band at a fixed R/C over 0–85 °C)
  brackets our ± 5 % target over a wider temperature range — ambitious but
  in-family, with the ± 10 % fallback as the honest backstop.
- Mode functions (defrost / reheat / bagel) are a deliberate exclusion here,
  not an oversight: CLAUDE.md holds them out until the basic timer, the
  temperature input and the hold-down driver are verified.

## Sources

- PT8A2514A datasheet — Diodes Inc.,
  <https://www.diodes.com/assets/Datasheets/PT8A2514A.pdf> (accessed
  2026-11-19).
- PT8A2515 datasheet — Diodes Inc.,
  <https://www.diodes.com/assets/Datasheets/PT8A2515.pdf> (accessed
  2026-11-19).
- r/nicechips thread that started this repo —
  <https://www.reddit.com/r/nicechips/comments/mor0ze/diodespericom_pt8a2514a_toaster_controller_ic_all/>
  (linked from the README; accessed 2026-11-19).

## Verification

Not a testable parameter row — this record is the market-review input. The
rows it grounds are verified by their own rows' methods.
