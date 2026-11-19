# sim-harness-core

The generic ~40% of an analog canary's `sim/` harness — PDK resolution, the
classic five-corner MOS sweep, fragment-based ngspice deck composition, and
append-only evidence recording. Scaffolded by `everyblock/scaffold.sh` into
`sim/harness/` for the `analog` and `mixed-signal` variants (block and chip
kind alike), so a new canary starts from working generic mechanics instead
of re-deriving the split between generic and block-specific by hand-reading
an existing canary's `sim/harness/` package end to end.

**Do not fork or vendor a sibling canary's harness wholesale.** Read one for
reference — `2AMLogic/gf180-sar-adc`'s `sim/harness/` is the implementation
this package was distilled from — but this package *is* the generic core
already extracted from that reading. Extend it in place per block; don't
re-derive it.

## What's here

| Module | Generic core covers |
|---|---|
| `pdk.py` | PDK discovery: env var / `PDK_ROOT` / `sim/pdk.local.json` / `sim/pdk.json` / built-in search-root resolution order. PDK name, variant scheme, and model-file paths come from `sim/pdk.json` (block-authored config, not part of this package) rather than being hardcoded. |
| `corners.py` | The classic five MOS process corners (`tt`/`ff`/`ss`/`fs`/`sf`) as the default corner set, plus the PVT-grid arithmetic (`Corner`, `PvtPoint`, `build_grid`, `supply_points`, `resolve_corners`) and the `sabotage()` negative control. |
| `toolchain.py` | Toolchain pin/drift checking against `sim/toolchain.json` (PDK version, ngspice floor, Python floor). Fully generic already — no block-specific content. |
| `testbench.py` | The `tb.json` manifest schema: netlist-fragment discipline (forbidden directives and reserved PVT parameter names the harness owns), `checks` validation, `netlist_provenance` semantics. Fully generic already. |
| `runner.py` | `compose_deck()` — assembles one complete ngspice deck from a testbench fragment, the resolved PDK's models, and one PVT point's corner sections. `run_point()` / `run_grid()` execute it and parse measurements. |
| `evidence.py` | `BaseExtensions` — an open, unstructured evidence-record extension slot (`extra` dict + `notes`), so a fresh block has a working evidence path before it has ratified its own schema. |
| `report.py` | Record-id minting (`<YYYYMMDD>-<HHMMSS>-<short-sha>`) and exclusive reservation of the record's `corners/<record-id>/` evidence bundle (raw per-point logs and composed decks, `INCOMPLETE` until the record is written), grid summaries, per-axis sensitivity, spec-check evaluation, and an append-only Markdown writer. |
| `strict_json.py` | The `object_pairs_hook` both file loaders (`tb.json`, `toolchain.json`) decode through, refusing an object that declares the same key twice — the stdlib decoder would otherwise silently keep only the last value. Fully generic already — no block-specific content. |
| `cli.py` | `list` / `run` / `selftest` subcommands wiring the above together. |

Every module's docstring opens with a `GENERIC CORE` paragraph (what it is
safe to use unmodified) and, where one exists, an `EXTENSION POINT`
paragraph (what a block is expected to add, and how). Read those before
editing — they say which changes belong in *this* package versus which
belong in a block-specific replacement or subclass.

## Extension points — what a block layers on top

This core deliberately does not know:

- **This PDK's device model section names**, beyond the assumption that a
  `tt`/`ff`/`ss`/`fs`/`sf` sweep is the useful default (`corners.py`). Most
  open PDKs' ngspice model libraries use exactly these section names for the
  MOS family; if this PDK's do not, edit the `sections` tuples on
  `corners.CORNERS`' built-in entries — that is the one PDK-specific edit
  most blocks need before their first run.
- **Block-specific corners.** A capacitor-dominated block (e.g. a SAR ADC's
  CDAC) or a resistor/BJT-dominated one (e.g. a bandgap) needs corners this
  core does not define. Call `corners.register_corner()` /
  `register_corner_set()` to add them without editing the built-in set.
- **Block-specific evidence-schema fields.** Once a block has ratified its
  own evidence convention (which fields every record must carry — FFT
  metadata, linearity methodology, Monte Carlo convention, whatever this
  block's spec doc names), replace `evidence.BaseExtensions` with a subclass
  or a fresh dataclass carrying named, *validated* fields instead of routing
  everything through the generic `extra` bag. See
  `2AMLogic/gf180-sar-adc`'s `sim/harness/evidence.py` for a worked example:
  five named extension groups, each with its own required-field and
  allowed-value validation.
- **Block-specific netlist injection ahead of the testbench.** A block
  needing PDK-variant-bound subckt aliases (the SAR-ADC reference's MIM
  capacitor wrapper is the worked example) passes an `extra_subckts`
  callable to `runner.compose_deck()` / `run_point()` / `run_grid()` rather
  than editing `compose_deck()` itself.
- **A rendered Markdown record layout beyond the plain one in
  `report.render_markdown()`.** `report.build_record()` already returns
  everything a richer renderer needs (grid, summary, sensitivity,
  evidence, environment, and a `reconciliation` block comparing the
  requested PVT points with the supplied results); replace only the rendering function once a
  block's evidence convention wants a specific layout (a toolchain-drift
  banner, a rendered per-axis sensitivity table, etc.).

## Result reconciliation

`report.build_record()` requires a nonempty requested grid and exactly one
result per requested PVT point, matched on actual (process, temperature,
supply) identity rather than rounded artifact names. Missing, duplicate and
unexpected results, or an empty grid, give `status: "error"` (never `pass`)
with counts and samples under `record["reconciliation"]`; raw results,
error records and completed-point statistics are still recorded. Result
order does not matter.

`run` applies the same reconciliation to its exit code. A recorded run
exits 0 only when its record's status is `pass` or `characterized`, so the
exit code and the record verdict always agree. A `--no-write` run, which
saves no record, also requires complete reconciliation, all points ok and
every limit met. A mismatch prints its problems and sample identities to
stderr and exits 1. A recorded run still writes its error record first.

## Configuration this core reads (block-authored, not shipped here)

- `sim/pdk.json` — required. `{"name": ..., "default_variant": ...,
  "model_lib": "path/relative/to/variant/dir", "design_include": "...",
  "search_roots": [...]}`. See `pdk.py`'s module docstring for the full
  shape. Discovered install dirs (env var, `PDK_ROOT`, `search_roots`) are
  anchored to an absolute, lexically normalized path (`os.path.abspath`; no
  symlink resolution) against the cwd at discovery time, so a relative root
  such as `PDK_ROOT=../pdks` keeps working when ngspice runs in its own
  workdir. The `.include`/`.lib` lines in the deck are always absolute.
- `sim/pdk.local.json` — optional, git-ignored, same shape, overrides
  `pdk.json` per-machine.
- `sim/toolchain.json` — optional. `{"pdk_version": "...",
  "ngspice_min_major": N, "python_min": "3.11"}`. Absent means "nothing
  pinned" (reported, not a failure), as does a file holding only
  `_`-prefixed commentary keys. Present keys are validated before any drift
  comparison: unknown non-`_` keys, a blank or non-string `pdk_version`, an
  `ngspice_min_major` that is not a positive integer (null, boolean,
  fractional all rejected) and a `python_min` that is not a dotted numeric
  version are a `PinsError` naming the file and field; `run` exits 1 with
  that diagnostic before simulating or reserving a record. Mappings passed
  to `toolchain.check()`/`summary()` are validated the same way. A file that
  declares the same key twice — `_` commentary keys included — is likewise
  rejected at parse time, naming the file and the duplicate key
  (see `strict_json.py`).
- `sim/<experiment-slug>/testbench/tb.json` — one per experiment. See
  `testbench.py`'s module docstring for the manifest schema.

  Numeric fields are validated when the manifest loads, with errors naming
  the field: `nominal_supply_v` must be a finite number > 0;
  `supply_tolerance` a finite fraction with 0 <= tol < 1; `temperatures_c` a
  non-empty array of finite numbers; every `checks` limit (`min`, `max`,
  `*_spread_pct`, `*_spread_pct_by_axis` values) a finite number. Booleans,
  strings, `NaN` and `Infinity` are rejected. A measurement that parses to a
  nonfinite value (e.g. `1e999`) fails its simulation point; the log is kept.
  A `tb.json` that declares the same key twice — at the top level, in a
  `checks` entry or inside a `*_by_axis` map, even when both values are
  identical — is rejected when the manifest is parsed, naming the file and
  the duplicate key (see `strict_json.py`); the decoder would otherwise
  silently keep only the last value.

  `analyses` (default `["op"]` when omitted) must be a non-empty array and
  `options` (default none) an array, each of non-blank strings; a bare
  string, object, `null`, non-string or blank entry is rejected at load,
  naming the manifest, field and entry index, before any simulator launch.
  Entries are full ngspice command / option text (e.g. `"tran 1n 100n"`);
  there is no command allowlist.

  Top-level keys are a closed set (`testbench.MANIFEST_KEYS`): an unknown
  key (`temperaturs_c`, `chekcs`, `corner`, ...) is rejected at load, naming
  the manifest, the unknown fields and the supported keys, so a typo cannot
  silently fall back to a default. Block-specific data goes in the `evidence`
  block (`evidence.from_manifest`); a block that deliberately reads extra
  top-level keys passes `testbench.load(dir, extra_keys=("my_key",))`.

  The netlist fragment keeps its own `.param` support under a **scope
  rule** (product#287): a *top-level* assignment to one of the three
  harness-owned names (`vdd_val` / `vdd_nom` / `temp_c` — any letter case,
  several assignments per directive, split across `+` continuation lines)
  is rejected when the manifest loads, before anything is frozen,
  reserved or launched — and again at deck composition, so a hand-built
  Testbench cannot bypass it — naming the fragment path and line, because
  it competes with the definition the harness emits ahead of the fragment
  include and would make the recorded PVT tuple ambiguous. Everything else
  stays supported: ordinary custom parameters, expression or comment
  *uses* of the reserved names (`.param ratio='vdd_val/2'` reads the
  harness's value), and subcircuit-local parameters — a `.param` inside a
  `.subckt` ... `.ends` body, or on the `.subckt` line itself. Move an
  intended supply/temperature choice to `nominal_supply_v` /
  `supply_tolerance` / `temperatures_c`, whose manifest `params` collision
  check this extends.

  A corner-matrix manifest must also carry at least one executable limit in
  `checks`; omitted, empty and description-only checks are rejected at load
  time (before any simulator launch or evidence reservation). A
  measurement-only experiment declares `"evidence": {"record_kind":
  "characterization", "data_provenance": "..."}`; its successful report reads
  `CHARACTERIZED` (status `characterized`), never "all spec checks satisfied".

## Running it

From `sim/`:

```
python3 -m harness.cli list
python3 -m harness.cli run <experiment-slug> --corners mos
python3 -m harness.cli selftest <experiment-slug>
```

### Which corners a run uses

`run` and `selftest` pick their process corners in this order:

1. `--corners a,b,...` on the command line, when given — an explicit
   override, always authoritative;
2. otherwise the testbench manifest's `corners` array (`tb.json`), e.g.
   `"corners": ["mos"]` or a set the block registered with
   `register_corner_set()`;
3. otherwise, when the manifest has no `corners` key, the generic
   `corners.DEFAULT_CORNER_SET` (the five MOS corners).

An empty selection (`--corners ""`, `"corners": []`, a blank name) or a name
that is neither a registered corner nor a registered corner set is refused
with a one-line `error:` and a nonzero exit (2 for a bad selection, 1 for a
malformed manifest) *before* the PDK is resolved, any simulator worker
starts, or any evidence is written. `selftest` additionally needs at least
two corners with different model sections, since one corner cannot show that
corner switching takes effect.

### Toolchain preflight (`run` and `selftest`)

Both commands run the same pin preflight against `sim/toolchain.json`, after
corner selection and PDK resolution and before any scratch workspace is
allocated or grid launched. Malformed pins exit 1; drift (PDK revision
mismatch or unknown revision, ngspice below the floor or unparseable, Python
below the floor) exits 3, with zero simulator calls. A missing or
comment-only pin file keeps the unpinned semantics. `--allow-toolchain-drift`
(accepted by both) overrides drift only; `selftest` then prints each drift
and a warning on stderr, since it saves no Environment section, and the
verdict is qualified by it. It never bypasses malformed-pin validation.
`selftest` still writes no evidence record.

**Behaviour change.** Earlier versions of this core
ignored the manifest's `corners` on a plain `run`/`selftest` and always
swept the generic default unless `--corners` was passed. A manifest that
sets `corners` now gets exactly the selection it asks for — check existing
manifests' `corners` values before re-running, since a manifest naming a
smaller (or unregistered) set will now produce a different grid (or a
selection error) than it did before.

### Recording a subset of the PVT matrix

The mandated matrix is at least three process corners at -40/27/125 C and
nominal supply +/-10 %, with every process/temperature/supply tuple present
(`report.matrix_conformance`). A **recorded** `run` whose selected points
fall short of that is refused — one-line `error:` naming the gaps, exit 2 —
unless it carries a nonblank justification:

```
python3 -m harness.cli run <experiment-slug> --corners tt,ff \
    --subset-reason "bring-up: slow corners blocked on <issue>"
```

The check runs after corner selection and before the scratch workspace,
evidence bundle or any simulator worker exists, so a refused run leaves
nothing behind. The reason is recorded verbatim as the record's
`Justification:` line; it documents an intentional limitation and does **not**
turn the run into full-matrix evidence — the record still says "Subset of the
mandated PVT matrix" with its gaps, and evidence review judges whether the
reason is adequate. Full-matrix runs need no reason. `--no-write` and
`--sabotage` runs record nothing, so they explore reduced grids freely.

**Behaviour change.** Earlier versions recorded such a subset with
"Justification: (none given)". Scripts that record a reduced matrix must now
pass `--subset-reason` (or `--no-write`, if the run was never meant as
evidence).

**`--supersedes <record-id>`** links a corrected record to the one it
replaces. On a recorded run the value (surrounding whitespace stripped) must
name a finished record of the same experiment, `sim/<slug>/records/<record-id>.md`
— a netlist snapshot or `corners/<record-id>/` bundle alone does not count —
or the run is refused before anything is created (exit 2, error listing the
nearest existing ids). Blank means no supersession; `--no-write` and
`--sabotage` runs skip the check.

### Where a run writes

| Output | Location |
|---|---|
| Recorded run's record, netlist snapshot | `sim/<slug>/records/`, `sim/<slug>/netlist-snapshots/` (append-only) |
| Recorded run's per-point logs and retained decks | `sim/<slug>/corners/<record-id>/` (the evidence bundle; `INCOMPLETE` until the record is written) |
| Frozen inputs of a recorded run (exact `tb.json` and netlist fragment bytes) | `sim/<slug>/corners/<record-id>/frozen/` (read-only where the platform allows) |
| Frozen inputs of a `--no-write` / `--sabotage` run and of `selftest` | `frozen/` inside that invocation's scratch workspace (nothing permanent) |
| Executed decks and simulator scratch (`run`) | `<parent>/run-<UTC>-<random>/`, parent = `--workdir` or `sim/<slug>/_build/` |
| `--no-write` / `--sabotage` decks and logs | `<parent>/nowrite-…/` or `<parent>/sabotage-…/` (nothing under `corners/`) |
| `selftest` grids | `<parent>/selftest-…/normal/` and `…/sabotaged/`, parent = `--workdir` or `sim/<slug>/_selftest/` |

Every `run` / `selftest` invocation creates its **own** scratch workspace
under the parent (atomically, with `tempfile.mkdtemp`) before it reserves a
record or starts any simulator, and prints its location as `scratch
workspace: …`. `--workdir` names that parent, not the workspace itself; a
relative path resolves against your current directory. Two concurrent
invocations of the same experiment — even with the same `--workdir` and the
same point ids — therefore never read each other's deck or overwrite each
other's simulator output, so a recorded bundle's deck is always the deck its
run executed. Inside one workspace, per-point files keep their usual
`<corner-id>.*` names. If the workspace cannot be created, the run exits 1
before anything is launched or reserved.

**Frozen inputs (product#229).** `load()` reads `tb.json` and the netlist
fragment once; parsing, validation, the recorded `netlist_sha256` /
`manifest_sha256` and the `netlist-snapshots/<record-id>.spice` header and
body all derive from those captured bytes. Before any point launches, `run`
and `selftest` write them to a run-owned `frozen/` directory (exclusive
create, verified, read-only best effort) and every composed deck
`.include`s that absolute path, so editing `testbench/` mid-run cannot mix
circuit revisions into a grid or make a record hash later bytes. A recorded
run's retained decks therefore point into its own bundle, not scratch. The
include path is absolute at run time; replaying a retained deck from a
moved checkout needs that path rewritten. Invalid manifests or fragments are
rejected inside `load()`, before a workspace, bundle or simulator exists.

**Retention and cleanup.** Workspaces are never deleted automatically, so a
failed or interrupted run's decks, logs and simulator output stay available
for diagnosis. No record links into them — recorded evidence lives only in
`corners/<record-id>/` — so any workspace not in use by a running invocation
can be removed at any time, e.g. `rm -rf sim/<slug>/_build sim/<slug>/_selftest`
when no run is active. Scaffolded repos git-ignore `sim/*/_build/` and
`sim/*/_selftest/`. (Earlier versions reused one `_build/` / `_selftest/`
directory for every run, and wrote disposable logs to `corners/_scratch/`;
those leftovers can be deleted.)

`harness/` needs no third-party dependencies — everything above is Python
standard library, so it runs the same way in a fresh agent container as on a
developer box, once `sim/pdk.json` points it at an installed PDK and
`ngspice` is on `PATH`.

## Why extend in place rather than fork this package per block

The five/eight-corner sweep, the deck-composition mechanics, the
toolchain-drift guard, and the append-only record writer are identical work
across every open-PDK analog canary this scaffold produces. A block that
forks this package and diverges immediately loses the ability to pull a
future core fix (a corrected `sabotage()` self-test, a `toolchain.py` bug
fix) without a manual merge. Extending through the documented extension
points — subclassing, registration functions, injected callables — keeps
that path open.

## Migration: per-point artifact filenames (product#218)

Per-point deck/log names are `<process>_<temp>c_<supply>v.{spice,log}`. The
supply used to be fixed at two decimals, so 1.801 V and 1.804 V both wrote
`tt_27c_1.80v.*` and overwrote each other. The supply now keeps at least two
decimals and as many more (up to six) as the value needs: `1.80`, `1.62`,
`1.98` are unchanged (the default grid's names and order are identical),
while `1.801` becomes `tt_27c_1.801v.*`. Only grids with sub-0.01 V supply
resolution see new filenames. Consumers that rebuild names by hand should
read `corner_id` (or the record's `deck`/`log` links) instead. Already
recorded artifacts are not renamed or rewritten. A grid in which two points
still share an id is rejected up front (`run` exits 2; `run_grid` raises
`corners.DuplicateArtifactError`) before any worker starts.
