# D-004: Cancel and fault behaviour

Status: proposed

Covers target-table rows 6 (cancel input), 7 (power-on state), 8 (NTC fault
behaviour), 9 (max-time limit) and 10 (supply-loss behaviour).

## Context

The pre-ratification table had one fault-flavoured row (cancel within 50 ms)
and left the rest of the safety picture unstated. Ratification requires the
full picture: power-on state, open/shorted-NTC behaviour, a maximum-time
limit that releases regardless of the timing core, and behaviour at supply
loss. This record fixes those semantics; these rows are the safety
invariants of the part and carry a single required value each.

## Options considered

1. **Separate cancel pin and separate lever-latch pin.** Two pins; the 8-pin
   budget (row 12) is already fully allocated (D-006).
2. **Lever input doubles as cancel** — down = valid start and hold; up =
   cancel. On a mechanical pop-up toaster the lever coming up IS the cancel
   gesture; a separate pin buys nothing at this architecture level.
3. **Button-matrix cancel** (comparison-part style). Rejected with the
   button modes: excluded until the basic timer is verified (CLAUDE.md).

## Decision (kept, safety semantics added)

Option 2. One pin (LEVER_N, pin 5, internal pull-up, active low): assertion
below the decode-hysteresis floor starts a valid run; release (high) is the
cancel condition. Row 6 kept: hold released within 50 ms of the cancel
condition asserting (100 ms fallback, 10 ms stretch), measured to zero coil
current.

Safety invariants, one per pre-ratification gap:

- **Power-on (row 7):** hold-down released from POR release until a valid
  start — lever down, browning level decoded, no NTC fault latched. The
  comparison part states the same property for its relay: "Power on reset
  circuit ... will reset all the logic circuit and the timer; it will clear
  all output pins when power on."
- **NTC fault (row 8):** open or shorted NTC disables compensation and the
  selected time stands (D-002); the run still ends by the max-time limit.
- **Max-time limit (row 9):** any hold ends by 1.25 × the maximum settable
  time — 300 s at the target range — regardless of timing-core state,
  including a counter that is stuck, running fast, or corrupted. The
  ceiling runs on its own time base, independent of the timing core, and
  that time base carries its own error budget: its worst-case behaviour is
  specified over the full supply range from 5.5 V down to BOR (row 10), so
  the bound holds through the degraded band where row 3's accuracy does not
  apply (see the supply-sag reconciliation in the spec README). Fallback:
  fixed 300 s absolute ceiling. The comparison part ships an analogous
  monitor ("Relay output disable without external oscillator").
- **Supply loss (row 10):** hold released when VDD falls below the
  brown-out threshold (≈ 2.7 V falling, hysteresis ≥ 150 mV — threshold
  specified once, in D-005); the released state persists until a valid
  start. With the supply gone the low-side driver is off and the coil
  de-energizes — release is the unpowered state, not an active drive.

## Rejected alternatives

- Option 1: spends a pin the budget does not have; the mechanical lever is
  already the user's cancel gesture.
- Option 3: button-driven control is out of scope until the basic timer, the
  temperature input and the hold-down driver are verified.

## Consistency with other groups

- 1.25 × 240 s = 300 s (row 9 vs. row 2's target range): the ceiling is
  consistent with the target range by construction; if the stretch range is
  adopted the ceiling moves to 375 s with it.
- Row 10's threshold lives in D-005 (supply) so BOR and POR thresholds are
  specified once; this record owns the behaviour, D-005 owns the number.

## Sources

- PT8A2514A datasheet,
  <https://www.diodes.com/assets/Datasheets/PT8A2514A.pdf> (accessed
  2026-11-19): POR clearing all outputs at power-on; "Relay output disable
  without external oscillator" (their fault monitor); "when timer is out,
  it will pull down Defrost, Reheat pins to release relay".

## Verification

Power-on and release sequencing in cocotb (POR sweep, lever-gated start,
cancel-to-zero-current bound); randomized runs plus counter-corruption and
oscillator-stall injection against the row 9 ceiling in cocotb; ceiling
time-base error across the ngspice supply grid down to BOR (`klt sim`), so
the 300 s bound is checked through the degraded band, not only the target
range; BOR threshold and hysteresis sweep in ngspice via `klt sim`.
