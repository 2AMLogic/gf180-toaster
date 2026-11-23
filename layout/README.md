# layout

GDS and DRC/LVS reports for the gf180-toaster block. The harness came up
with issue #16; until real cells exist, this directory carries the layout
smoke — one placeholder cell through the pinned `klt` — and its
append-only evidence records.

## One command

```
make smoke-layout                       # klt from PATH (CI: the project venv)
make smoke-layout KLT=/tmp/klt-pin/bin/klt   # a throwaway pinned install
python3 layout/run_smoke.py --klt <path>     # what the target runs
```

The runner checks the toolchain pin, generates the placeholder cell with
`klt gen`, grades it with `klt drc --deck gf180mcu`, runs a deliberately
violating negative control, and appends an evidence record. It exits 0
only when every step holds; a failed run writes no record and leaves its
partial bundle on disk, uncommitted. `make smoke` runs it alongside the
analog and digital smokes.

## What is here

| File | Role |
|---|---|
| `toolchain.json` | The klt pin (`klt_install`, `klt_required_commands`, `klt_last_verified_commit`, `klayout_package_min`) plus its append-only comment history. Every pin bump is a new comment paragraph naming the issue — never an edit to an earlier one. |
| `toolchain_pin.py` | The ONE shared required-commands preflight every layout runner imports: each `klt_required_commands` entry must appear in `klt --help`'s subcommand list, or the runner exits 1 naming the command and the install line. |
| `run_smoke.py` | The layout smoke runner described above. Also verifies the running klt's git commit equals `klt_last_verified_commit`, so a dev build cannot stand in for the pin. |
| `test_toolchain_pin.py` | Stdlib unit tests for the preflight (fake `klt` missing a command → exit 1 naming it). In `make test-unit`'s `UNIT_TESTS` list. |
| `records/` | Append-only evidence records: `<YYYYMMDD>-<HHMMSS>-<short-sha>.md` in the `sim/smoke-op/records/` id style, each with its bundle directory holding the GDS streams and DRC reports. Records are only added, never edited; a superseded result gets a later record saying why. |

## Generic vs specific to this block

In the style of `sim/harness/README.md`:

**Generic pattern, taken from siblings** (the part a new canary block
would copy; the friction of copying it is filed upstream as
2AMLogic/klayout-tools#7331 — offer the preflight as a `klt` verb, with
#7324 as the machine-readable subcommand list it would build on):

- `toolchain_pin.py` — the pin-document schema and the
  required-commands/subcommand-list check are block-agnostic. The pattern
  comes from sg13g2-sat-rx's `layout/toolchain_pin.py`; this repo's copy
  drops that sibling's PDK-primitive-GDS helpers because gf180-toaster's
  runners hand PDK resolution to `klt`'s own `--pdk` flag.
- `run_smoke.py`'s skeleton — preflight → generate → DRC → negative
  control → append-only record — is the layout-side smoke shape any
  block can reuse.

**Block-specific** (this repo's choices, what a reader must not treat as
convention):

- The pin values in `toolchain.json` (`klayout-tools==0.9.2` and its
  commit, `klt_required_commands` = the verbs this repo's runners invoke).
- `run_smoke.py`'s constants: PDK variant `gf180mcuD` (the same open_pdks
  revision `sim/toolchain.json` and CI pin), deck `gf180mcu`, placeholder
  generator `res_array` at default params, and the negative control's
  squeezed `spacing_um`.
- Everything under `records/`.

## Coverage caveat

`klt drc --deck gf180mcu` runs klayout-tools' **curated** deck — a
deliberately partial subset of the PDK's design rule manual, not the
foundry signoff deck (see klayout-tools `docs/cli/drc.md`, "Coverage").
A clean smoke record proves the harness path works; it is not a signoff
claim. LVS and extraction runners (and the `extract`/`lvs` entries in
`klt_required_commands`) arrive with the first real layout issue.
