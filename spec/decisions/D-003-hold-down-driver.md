# D-003: Hold-down driver

Status: proposed

Covers target-table row 5 (hold-down output).

## Context

A pop-up toaster holds the carriage down with a solenoid while toasting and
releases it when the time is out (or the lever is lifted). The chip must sink
the solenoid's hold current for the whole toast. The pre-ratification table
fixed the shape — a low-side driver, ≥ 50 mA target, ≥ 150 mA stretch, for a
"holding solenoid or an external transistor" — but not the device class, the
voltage bound, or the coil-clamp requirement.

## Options considered

1. **Low-side NMOS driver (6 V device class), on-chip, driving the solenoid
   directly** at the ≥ 50 mA target. The PDK's 6 V devices exist for exactly
   this kind of load (confirmed: the gf180mcu spice model family is a
   "0.18um 3.3v/6v High Voltage MCU Process").
2. **LED-class output + external transistor** (what the comparison part
   does: its pins sink or source tens of milliamps at most — absolute
   -maximum output current 20 mA — and its application circuit uses an
   external 8050 NPN with a 1N4004 clamp to drive the relay coil). Minimal
   chip risk, but makes the external transistor mandatory even at the target
   current, and an on-chip ≥ 50 mA sink is exactly the kind of integration
   this part exists to demonstrate.
3. **High-side PMOS driver.** Appliance harnesses return the solenoid to
   GND; a high-side switch flips that convention for no gain.

## Decision (kept, bounds made explicit)

Option 1. On-chip low-side driver on pin 2 (DRV): sinks ≥ 50 mA at
VOL ≤ 0.6 V across the `klt sim` corner grid (target); ≥ 30 mA fallback,
with the external transistor mandatory above that; ≥ 150 mA stretch. The
DRV pin's absolute maximum is 6 V; an external coil clamp (flyback diode or
snubber) is a board requirement, stated in the pin list and repeated in the
datasheet-to-be — the comparison part's application circuit does the same
(1N4004 across the coil).

## Rejected alternatives

- Option 2 as the only path: retained as the documented fallback (≥ 30 mA +
  external transistor) rather than the target.
- Option 3: inverts the harness convention for no area or accuracy gain.
- On-chip clamp: duplicates a commodity board diode and eats area at the
  stretch current (the coil's stored energy dumps into it on every release);
  the external clamp also protects the pin during board faults. Revisit only
  if board-BOM pressure demands it.

## Consistency with other groups

- Row 1 (D-005): a 5 V ± 10 % rail tops at 5.5 V; the 6 V device class keeps
  the DRV pin rating above rail plus clamp transients by construction.
- Row 12 (D-006): one pin for the driver.
- Row 6 (D-004): the 50 ms cancel bound is measured to zero coil current,
  so it includes the driver's turn-off, not just the gate command.

## Sources

- gf180mcu PDK,
  <https://gf180mcu-pdk.readthedocs.io/en/latest/analog/Analog.html>
  (accessed 2026-11-19): "FAB3E Spice Model For 0.18um 3.3v/6v High Voltage
  MCU Process" — the 6 V device class the driver is built from.
- PT8A2514A datasheet,
  <https://www.diodes.com/assets/Datasheets/PT8A2514A.pdf> (accessed
  2026-11-19): absolute-maximum output current 20 mA; application circuit
  drives the relay coil through an external 8050 NPN with a 1N4004 clamp —
  the precedent for both the external-transistor fallback and the external
  clamp requirement.

## Verification

Driver characterization in ngspice across the `klt sim` corner grid: sink
current vs. VOL, gate-drive level, temperature. Layout: device rating and
pin map in DRC/LVS, electromigration check on the DRV pad stack via `klt`.
