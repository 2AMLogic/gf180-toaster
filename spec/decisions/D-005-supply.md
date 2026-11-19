# D-005: Supply

Status: proposed

Covers target-table rows 1 (supply voltage), 10 (supply-loss behaviour) and
11 (standby current).

## Context

The pre-ratification table fixed 5 V ± 10 % and ≤ 100 µA standby (≤ 20 µA
stretch) but left the brown-out behaviour and the reasoning behind the rail
unstated — and the standby budget interacts with the oscillator choice
(D-001) and the driver's device class (D-003).

## Options considered

1. **5 V ± 10 % single rail** (pre-ratification value). Matches the
   appliance context: the low-voltage supply is an external board rail; the
   commercial comparison part runs 3.5–5.5 V, typ 5.0 V.
2. **3.3 V single rail.** Cheaper logic, but the hold-down driver loses the
   6 V device headroom (D-003) for no functional gain, and the solenoid-hold
   budget tightens for nothing.
3. **Wide-range 3.0–5.5 V.** The stretch, not the target: wide-range biasing
   and a lower BOR window are unverified cost before the basic timer works.
4. **Dual rail** (5 V + 3.3 V). Two pins for power on an 8-pin part.

## Decision (kept, BOR added)

Row 1 kept: 5 V ± 10 % (4.5–5.5 V), fallback 5 V ± 5 % (a regulated rail
assumed), stretch 3.0–5.5 V single rail.

Row 11 kept: ≤ 100 µA standby with the lever up (fallback ≤ 250 µA, stretch
≤ 20 µA). The standby path powers only the POR/BOR supervisor and the lever
monitor — the timing oscillator and its RSET bias are gated off (D-001) —
which is what makes the budget reachable. The comparison part publishes
≤ 500 µA while running and no standby figure at all, so this bound is not
benchmarked against a published number; it is set by what the supervisor
circuitry needs.

Row 10 concrete: brown-out reset at ≈ 2.7 V falling with ≥ 150 mV
hysteresis; below the threshold the hold is released and stays released
until a valid start (behaviour owned by D-004). 2.7 V keeps the logic biased
and releases the coil well before the 4.5 V minimum rail, while staying
clear of POR-threshold ambiguity; the ratified value is the nominal — the
exact threshold is a corner-grid measurement (verification below).

## Rejected alternatives

- Option 2: driver headroom lost for no functional gain.
- Option 3 as target: wide-range biasing is unverified cost; kept as stretch.
- Option 4: two power pins on an 8-pin part is a non-starter.

## Consistency with other groups

- D-003: the 6 V device class covers the 5.5 V rail top plus clamp
  transients.
- D-001: oscillator and RSET-bias gating is what row 11's budget buys.
- D-004: row 10's behaviour is specified there; the threshold number is
  specified here, once.

## Sources

- PT8A2514A datasheet,
  <https://www.diodes.com/assets/Datasheets/PT8A2514A.pdf> (accessed
  2026-11-19): recommended VCC 3.5–5.5 V (typ 5.0 V), ICC ≤ 500 µA while
  running, operating temperature 0–85 °C.
- gf180mcu PDK, <https://gf180mcu-pdk.readthedocs.io/en/latest/> (accessed
  2026-11-19): 3.3v/6v device families bound the rail choices.

## Verification

Supply grid (4.5–5.5 V, plus the fallback and stretch spans) across the
`klt sim` corner grid in ngspice: oscillator period, driver sink current,
IDD. BOR threshold and hysteresis sweep in ngspice; release-at-BOR and
stay-released sequences in cocotb.
