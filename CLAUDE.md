# gf180-toaster — agent instructions

Open-source canary block: a Toaster controller on the gf180mcu PDK,
designed and verified by AI agents — a **mixed-signal** block.

- **PDK**: gf180mcu (open PDK).
  Digital: cocotb + Icarus, Yosys, OpenROAD. Analog: xschem + ngspice.
  Layout, DRC, and LVS go through klayout-tools (`klt`).
- **Friction protocol (the canary's job)**: every time klayout-tools is
  awkward, missing a capability, or wrong for what you need, file an issue at
  `2AMLogic/klayout-tools` describing the tool gap generically — that tracker is
  scoped to the tool, so keep design-specific detail (spec values, this repo's
  content) out of it and describe the gap, not the design.
- **Verification is the product**: no claim without a testbench. Recorded
  results are append-only evidence; a result is not superseded by deletion, it
  is superseded by a later record that says why.
- Spec changes go through `spec/` with a decision record. Agents do not relax
  the ratified spec to make a result pass.
- **Ratification is a merged PR, and the status line merges with it.**
  Ratifying a decision record is the act of that record's own PR being
  reviewed and merged, after two independent reviews (engineering and
  market) by sessions that did not author the PR or the design. The
  reviewer instructions are read centrally, not from this repo. Flip the
  record's own `Status:` line from `proposed` to `RATIFIED` **in the PR
  itself**, never as a follow-up commit.

- **This is a timer chip, not a mains design.** Nothing in this repo connects to line voltage, and no document here gives mains wiring advice. Keep the first version to the basic browning timer: do not add modes, displays or a microcontroller before the timer, the temperature input and the hold-down driver are verified.

## Harness bootstrap

The analog half (oscillator, comparator, driver) starts from the generic core already in `sim/harness/`; [gf180-rcosc](https://github.com/2AMLogic/gf180-rcosc) is the worked example of an oscillator extension. The digital half (the timer counter) chains to [gf180-trng](https://github.com/2AMLogic/gf180-trng)'s cocotb harness.

<!-- BEGIN LOOM ORCHESTRATION -->
This repository uses [Loom](https://github.com/rjwalters/loom) for AI-powered development orchestration — see the Loom repository for the full guide (roles, labels, worktrees, configuration). When installed, Loom also writes a locally-substituted copy of that guide to `.loom/CLAUDE.md`.

Work is coordinated through `loom:` labels on issues and pull requests, and the same roles run either under `loom-daemon` or by hand in an attended session — daemon mode is optional. Create the labels once with `.loom/scripts/sync-labels.sh` (an install ships `.github/labels.yml` but does not create the labels on the forge). A pull request ready for review carries `loom:review-requested`; Judge reviews it and applies `loom:pr` (approved) or `loom:changes-requested`; Doctor fixes a `loom:changes-requested` pull request and returns it to `loom:review-requested`. Only a `loom:pr` pull request gets merged, and always via this repo's merge script (`.loom/scripts/merge-pr.sh`) — never a raw forge merge command such as `gh pr merge`. Full state machine: `.loom/docs/label-state-machine.md`.
<!-- END LOOM ORCHESTRATION -->
