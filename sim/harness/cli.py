"""Command-line entry point wiring the generic core together.

GENERIC CORE. Three subcommands, each a thin driver over the modules next to
this file:

    list      discover(sim/) and print every experiment with a testbench
    run       load a testbench, select its corners (--corners if given, else
              the manifest's ``corners``, else corners.DEFAULT_CORNER_SET),
              resolve the PDK, check toolchain pins, build the PVT grid, run
              it, and (unless --no-write) write an append-only Markdown
              record. Selection is validated before anything is written, and
              a recorded run that is a subset of the mandated PVT matrix is
              refused unless --subset-reason gives a nonblank reason. A
              recorded run first reserves its record id by creating
              corners/<record-id>/, so each point's raw log and composed deck
              land in a bundle no other run shares; a bundle whose record was
              never written keeps an INCOMPLETE marker instead of being
              deleted
    selftest  run a testbench's selected corners (same selection rule as
              ``run``; needs >= 2 distinct corners) twice -- once normally,
              once sabotaged (corners.sabotage) -- and fail if the sabotaged
              run's per-axis sensitivity does not collapse. This is the
              harness's own negative control; see corners.sabotage's
              docstring for what it catches. Runs the same toolchain pin
              preflight as ``run`` (after corner/PDK resolution, before any
              workspace or grid); --allow-toolchain-drift overrides drift
              with a printed warning but never malformed pins.

Scratch workspaces: every ``run`` / ``selftest`` invocation simulates in its
own freshly created directory under the scratch parent -- ``--workdir`` when
given (relative paths resolve against the caller's cwd), else
``sim/<slug>/_build/`` (run) or ``sim/<slug>/_selftest/`` (selftest). Two
concurrent invocations of the same experiment therefore never share a deck,
log or simulator output file, while per-point filenames inside one
invocation's workspace stay ``<corner-id>.*``. Workspaces are kept after the
run (its location is printed) so failed runs stay diagnosable; nothing a
record links to lives there, so a workspace no run is using can be deleted
at any time.

EXTENSION POINT: a block wanting additional CLI flags (e.g.
``--allow-toolchain-drift``'s sibling for a block-specific override, or a
flag surfacing an extension corner set by name) should add a subparser here
that imports and calls into this module's functions rather than duplicating
them -- see gf180-sar-adc's harness/cli.py, which is this file's fuller,
block-specific descendant.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import sys
import tempfile
from pathlib import Path

from . import HARNESS_VERSION
from . import toolchain as toolchain_mod
from .corners import (
    Corner,
    CornerSelectionError,
    DuplicateArtifactError,
    build_grid,
    check_unique_artifact_ids,
    parse_corner_list,
    resolve_corners,
    sabotage,
    select_corner_names,
    supply_points,
)
from .pdk import Pdk, PdkConfigError, PdkNotFound, find_pdk
from .report import (
    RECORDS_DIR,
    RecordExists,
    axis_sensitivity,
    build_record,
    describe_failure,
    evaluate_checks,
    finalize_bundle,
    git_provenance,
    mark_incomplete,
    matrix_conformance,
    reconcile_results,
    record_exists,
    reserve_record_id,
    summarize,
    write_netlist_snapshot,
    write_record,
)
from .runner import NgspiceMissing, absolute_path, ngspice_version, run_grid
from .testbench import discover, freeze, load

SIM_DIR = Path(__file__).resolve().parent.parent


def cmd_list(args: argparse.Namespace) -> int:
    experiments = discover(SIM_DIR)
    if not experiments:
        print("no experiments with a testbench/tb.json under sim/ yet")
        return 0
    for path in experiments:
        print(path.name)
    return 0


def _resolve_pdk() -> Pdk | None:
    try:
        return find_pdk(SIM_DIR)
    except (PdkNotFound, PdkConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return None


def _select_corners(args: argparse.Namespace, tb) -> list[Corner] | None:
    """Resolve the run's corner selection, or print why not and return None.

    ``--corners`` (when given) overrides the manifest; otherwise the
    testbench manifest's ``corners`` is used (which itself defaults to
    corners.DEFAULT_CORNER_SET when the manifest omits it). Called before
    PDK resolution, simulator launch, or any evidence write, so a bad
    selection costs nothing.
    """
    try:
        cli_names = parse_corner_list(args.corners) if args.corners is not None else None
        return resolve_corners(select_corner_names(cli_names, tb.corners))
    except CornerSelectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def _allocate_workspace(parent: Path | str, kind: str) -> Path | None:
    """Create this invocation's exclusive scratch workspace under ``parent``.

    ``parent`` is ``--workdir`` (or the experiment's default scratch root);
    it is created if missing and made absolute against the caller's cwd.
    The workspace itself is ``<parent>/<kind>-<UTC timestamp>-<random>/``,
    created atomically by :func:`tempfile.mkdtemp`, so concurrent
    invocations sharing a parent each get a directory no other run writes
    to. Called once per invocation, before any record is reserved or
    simulator worker started; returns ``None`` (after printing why) if the
    workspace cannot be created.
    """
    parent = absolute_path(parent)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        parent.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix=f"{kind}-{stamp}-", dir=parent))
    except OSError as exc:
        print(f"error: could not create a scratch workspace under {parent}: {exc}",
              file=sys.stderr)
        return None
    print(f"scratch workspace: {workspace}")
    return workspace


#: Subdirectory (of the evidence bundle or scratch workspace) holding the
#: run's frozen copy of the manifest and netlist fragment.
FROZEN_DIRNAME = "frozen"


def _freeze_inputs(tb, destination: Path) -> bool:
    """Freeze ``tb``'s captured inputs into ``destination``; False (after
    printing why) if that fails. Nothing has been simulated yet."""
    try:
        freeze(tb, destination)
    except OSError as exc:
        print(f"error: could not freeze testbench inputs into {destination}: {exc}",
              file=sys.stderr)
        return False
    return True


def _subset_reason_ok(tb, points, reason: str) -> bool:
    """May this *recorded* run proceed, given its matrix coverage?

    A run covering the full mandated PVT matrix (report.matrix_conformance)
    needs no justification. A subset is only recorded with a nonblank
    ``--subset-reason``; otherwise print the gaps and return False. Called
    after point selection and before any workspace is allocated, bundle
    reserved, or simulator worker launched. The reason documents an
    intentional limitation -- the record still says "Subset of the mandated
    PVT matrix" with its gaps; it is not upgraded to full-matrix evidence.
    Disposable runs (--no-write / --sabotage) never reach this check.
    """
    matrix = matrix_conformance(tb, points)
    if matrix["full"] or reason.strip():
        return True
    print("error: this run is a subset of the mandated PVT matrix ("
          + "; ".join(matrix["missing"]) + "); a recorded subset needs "
          "--subset-reason \"<why>\" (or explore it with --no-write)", file=sys.stderr)
    return False


def _supersedes_ok(tb, supersedes: str) -> bool:
    """Does a *recorded* run's ``--supersedes`` name a prior record?

    ``supersedes`` is the append-only evidence trail's only link from a
    correction to the record it replaces, and a record cannot be edited
    once written, so a dangling reference would be permanent. Blank means
    "no supersession". Otherwise the (already stripped) value must name a
    finished Markdown record of this same experiment
    (report.record_exists: ``records/<id>.md`` only -- a snapshot or bundle
    alone does not count, and a malformed id never reaches a path join).
    On failure print the nearest existing ids and return False; nothing is
    auto-corrected. Called before any workspace is allocated, bundle
    reserved, or simulator worker launched. Disposable runs (--no-write /
    --sabotage) never reach this check.
    """
    if not supersedes or record_exists(tb.experiment_dir, supersedes):
        return True
    records_dir = tb.experiment_dir / RECORDS_DIR
    known = sorted(p.stem for p in records_dir.glob("*.md")) if records_dir.is_dir() else []
    if not known:
        hint = "this experiment has no records yet"
    else:
        near = difflib.get_close_matches(supersedes, known, n=3)
        hint = ("nearest existing records: " + ", ".join(near)) if near else \
            ("no similar record id; most recent: " + ", ".join(known[-3:]))
    print(f"error: --supersedes {supersedes!r} does not name an existing record in "
          f"{records_dir} ({hint}); nothing was simulated", file=sys.stderr)
    return False


def _toolchain_preflight(
    pdk, allow_drift: bool, warn_on_override: bool = False
) -> "int | tuple[str, dict]":
    """Shared pin preflight for ``run`` and ``selftest``.

    Returns ``(ngspice_version, toolchain_summary)`` when simulation may
    proceed, else the exit code to return (diagnostic already printed).
    Malformed pins are never bypassable; drift is refused unless
    ``allow_drift``. Absent pin files keep the unpinned semantics.
    ``warn_on_override`` prints the drift when it is overridden (for
    commands with no saved Environment section to record it in).
    """
    try:
        ngspice = ngspice_version()
    except NgspiceMissing as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:  # a malformed pin file must not read as a satisfied toolchain
        pins = toolchain_mod.load_pins(SIM_DIR)
    except toolchain_mod.PinsError as exc:
        print(f"error: invalid toolchain pins: {exc}\n"
              "Fix sim/toolchain.json (or remove the bad key); nothing was simulated.",
              file=sys.stderr)
        return 1
    drifts = toolchain_mod.check(pdk.version, ngspice, sys.version.split()[0], pins)
    if drifts and not allow_drift:
        print(toolchain_mod.format_drifts(drifts), file=sys.stderr)
        return 3
    if drifts and warn_on_override:
        print("warning: --allow-toolchain-drift given; proceeding on a toolchain that "
              "does not match sim/toolchain.json. This result is qualified by the "
              "drift below.", file=sys.stderr)
        for drift in drifts:
            print(f"  - {drift}", file=sys.stderr)
    return ngspice, toolchain_mod.summary(drifts, pins, allowed=allow_drift)


def cmd_run(args: argparse.Namespace) -> int:
    try:
        tb = load(args.experiment)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    corners = _select_corners(args, tb)
    if corners is None:
        return 2

    pdk = _resolve_pdk()
    if pdk is None:
        return 1

    pre = _toolchain_preflight(pdk, args.allow_toolchain_drift)
    if isinstance(pre, int):
        return pre
    ngspice, tc_summary = pre

    if args.sabotage:
        corners = sabotage(corners)
        args.no_write = True  # a sabotaged run must never enter the evidence tree

    temperatures = tb.temperatures_c
    supplies = supply_points(tb.nominal_supply_v, tb.supply_tolerance)
    points = build_grid(corners, temperatures, supplies)
    try:  # before any record/bundle is reserved or worker launched
        check_unique_artifact_ids(points)
    except DuplicateArtifactError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not args.no_write and not _subset_reason_ok(tb, points, getattr(args, "subset_reason", "")):
        return 2
    args.supersedes = (args.supersedes or "").strip()
    if not args.no_write and not _supersedes_ok(tb, args.supersedes):
        return 2

    started = _dt.datetime.now(_dt.timezone.utc)
    git = git_provenance(SIM_DIR.parent)

    # This invocation's own scratch workspace (see the module docstring),
    # allocated before any record is reserved so a failure here leaves
    # nothing behind that looks like evidence.
    workdir = _allocate_workspace(
        args.workdir or tb.experiment_dir / "_build",
        "sabotage" if args.sabotage else "nowrite" if args.no_write else "run",
    )
    if workdir is None:
        return 1
    if args.no_write:
        # Disposable run: decks, logs and the frozen inputs stay in the
        # workspace, nothing is reserved or recorded.
        if not _freeze_inputs(tb, workdir / FROZEN_DIRNAME):
            return 1
        return _simulate_and_record(
            args, tb, pdk, points, workdir, None,
            started, git, ngspice, tc_summary, record_id=None,
        )

    # Recorded run: reserve the record id -- and with it the evidence bundle
    # corners/<record-id>/ -- *before* simulating, so every raw log and deck
    # lands in a directory this run alone owns and no later run overwrites.
    try:
        record_id, bundle = reserve_record_id(SIM_DIR.parent, tb.experiment_dir, started, git)
    except OSError as exc:
        print(f"error: could not reserve an evidence bundle: {exc}", file=sys.stderr)
        return 1
    try:
        # Freeze before any point launches: every deck includes this copy,
        # and the bundle keeps the exact inputs beside the evidence.
        if not _freeze_inputs(tb, bundle / FROZEN_DIRNAME):
            mark_incomplete(bundle, "could not freeze testbench inputs")
            return 1
        rc = _simulate_and_record(
            args, tb, pdk, points, workdir, bundle, started, git, ngspice, tc_summary,
            record_id=record_id,
        )
    except BaseException as exc:
        # Interrupted or crashed: keep the diagnostics, say plainly that this
        # bundle has no finished record, and let the exception propagate.
        mark_incomplete(bundle, f"run aborted: {type(exc).__name__}: {exc}")
        raise
    if rc is None:
        mark_incomplete(bundle, "record could not be written (see stderr of that run)")
        return 1
    finalize_bundle(bundle)
    return rc


def _simulate_and_record(
    args, tb, pdk, points, workdir, log_dir, started, git, ngspice, tc_summary, record_id
) -> int | None:
    """Run the grid and, for a recorded run (``record_id`` set), write its
    snapshot and record. Returns the exit code, or ``None`` if a reserved
    record could not be written."""
    results = run_grid(
        tb, pdk, points, workdir, jobs=args.jobs, log_dir=log_dir, num_threads=args.num_threads
    )

    n_ok = sum(1 for r in results if r.status == "ok")
    print(f"{n_ok}/{len(results)} points ok")

    measure_names = list(tb.measure)
    summary = summarize(results, measure_names)
    sensitivity = axis_sensitivity(results, measure_names)
    failures = evaluate_checks(tb.checks, results, summary, sensitivity)
    for failure in failures:
        print(f"FAIL: {describe_failure(failure)}", file=sys.stderr)

    # Limit checks skip absent points, so a short, duplicated or foreign
    # result list would pass them vacuously: reconcile against the requested
    # grid (the same tuple rule build_record uses) before any verdict.
    reconciliation = reconcile_results(points, results)
    _print_reconciliation(reconciliation)

    if record_id is None:
        print("--no-write (or --sabotage): nothing recorded")
        print(f"decks and logs: {workdir}")
        ok = reconciliation["complete"] and not failures and n_ok == len(results)
        return 0 if ok else 1

    wall_seconds = (_dt.datetime.now(_dt.timezone.utc) - started).total_seconds()
    record = build_record(
        tb, pdk, points, results, ngspice, SIM_DIR.parent, record_id,
        started.isoformat(), wall_seconds, claim=args.claim or tb.claim,
        supersedes=args.supersedes or "", subset_reason=getattr(args, "subset_reason", ""),
        git=git, toolchain=tc_summary, evidence_dir=log_dir,
    )
    try:
        write_netlist_snapshot(tb, tb.experiment_dir, record_id)
        path = write_record(record, tb.experiment_dir)
    except RecordExists as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None
    print(f"wrote {path}")
    print(f"raw logs and decks: {log_dir}")
    # The exit status is the record's verdict, so the two cannot disagree.
    return 0 if record["status"] in PASSING_STATUSES else 1


#: Record verdicts a recorded `run` exits 0 for (``error`` / ``fail`` exit 1).
PASSING_STATUSES = frozenset({"pass", "characterized"})


def _print_reconciliation(reconciliation: dict) -> None:
    """Explain, on stderr, why a result set does not match its grid."""
    if reconciliation["complete"]:
        return
    print(f"ERROR: results do not reconcile with the requested grid "
          f"({reconciliation['results']} result(s) for "
          f"{reconciliation['requested']} requested point(s))", file=sys.stderr)
    for problem in reconciliation["problems"]:
        print(f"  - {problem}", file=sys.stderr)
    for kind in ("missing", "duplicate", "unexpected"):
        samples = reconciliation.get(f"{kind}_samples") or []
        if samples:
            shown = ", ".join(
                f"{s['corner']}/{s['temp_c']:g}C/{s['vdd']:g}V" for s in samples)
            more = reconciliation[kind] - len(samples)
            print(f"    {kind}: {shown}" + (f" (+{more} more)" if more > 0 else ""),
                  file=sys.stderr)


def _grid_incomplete(results, points, measure_names) -> str | None:
    """Why a selftest grid cannot support a sensitivity verdict, or None.

    A grid is usable only if it returned one result per requested point and
    every result is status ``ok`` with no ``missing`` and every requested
    measurement present (``axis_sensitivity`` silently drops anything else).
    """
    if len(results) != len(points):
        return f"returned {len(results)} results for {len(points)} requested points"
    bad = [r for r in results if r.status != "ok"]
    if bad:
        return f"{len(bad)}/{len(results)} points did not succeed (status {bad[0].status})"
    for r in results:
        absent = sorted({*r.missing, *(n for n in measure_names if n not in r.measurements)})
        if absent:
            return f"point is missing measurements: {', '.join(absent)}"
    return None


def cmd_selftest(args: argparse.Namespace) -> int:
    """Negative-control self-test: sabotaged corners must fail sensitivity."""
    try:
        tb = load(args.experiment)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Same selection contract as `run`: --corners, else the manifest, else
    # the generic default -- so the negative control exercises the corners
    # the testbench actually records evidence with.
    normal = _select_corners(args, tb)
    if normal is None:
        return 2
    if len({corner.sections for corner in normal}) < 2:
        names = ", ".join(corner.name for corner in normal)
        print(f"error: selftest needs at least two corners with different model sections "
              f"to show corner switching takes effect; selection resolves to: {names}",
              file=sys.stderr)
        return 2

    pdk = _resolve_pdk()
    if pdk is None:
        return 1

    # Same pin preflight as `run`, before any workspace or grid launch.
    pre = _toolchain_preflight(pdk, args.allow_toolchain_drift, warn_on_override=True)
    if isinstance(pre, int):
        return pre

    sabotaged = sabotage(normal)
    temperatures = tb.temperatures_c
    supplies = supply_points(tb.nominal_supply_v, tb.supply_tolerance)

    normal_points = build_grid(normal, temperatures, supplies)
    sabotaged_points = build_grid(sabotaged, temperatures, supplies)

    # One exclusive workspace per invocation, with the two grids as children,
    # so concurrent selftests never share a deck.
    workdir = _allocate_workspace(args.workdir or tb.experiment_dir / "_selftest", "selftest")
    if workdir is None:
        return 1
    if not _freeze_inputs(tb, workdir / FROZEN_DIRNAME):
        return 1
    normal_results = run_grid(tb, pdk, normal_points, workdir / "normal", jobs=args.jobs)
    sabotaged_results = run_grid(tb, pdk, sabotaged_points, workdir / "sabotaged", jobs=args.jobs)

    measure_names = list(tb.measure)
    # Simulator failure is not a collapsed sensitivity: both grids must be
    # complete and successful before process movement is judged. Scratch
    # dirs are kept for diagnosis.
    for label, results, points in (
        ("normal", normal_results, normal_points),
        ("sabotaged", sabotaged_results, sabotaged_points),
    ):
        problem = _grid_incomplete(results, points, measure_names)
        if problem:
            print(f"FAIL: {label} grid incomplete or failed — {problem}; "
                  f"diagnostics kept in {workdir}", file=sys.stderr)
            return 1
    normal_sensitivity = axis_sensitivity(normal_results, measure_names)
    sabotaged_sensitivity = axis_sensitivity(sabotaged_results, measure_names)

    process_moved_normally = any(
        (normal_sensitivity.get(name) or {}).get("process", {}).get("max_pct") not in (None, 0)
        for name in measure_names
    )
    process_moved_sabotaged = any(
        (sabotaged_sensitivity.get(name) or {}).get("process", {}).get("max_pct") not in (None, 0)
        for name in measure_names
    )

    # Sabotage keeps corner names, so a verifiable process comparison still
    # shows >= 2 levels; a measure with a zero mean has no defined spread, so
    # at least one measure must yield a measured (possibly zero) spread.
    sab_process = [(sabotaged_sensitivity.get(n) or {}).get("process", {}) for n in measure_names]
    if any(p.get("levels", 0) < 2 for p in sab_process) or all(
        p.get("max_pct") is None for p in sab_process
    ):
        print("FAIL: sabotaged grid has no measurable process comparison "
              "(fewer than two process levels or no defined spread)", file=sys.stderr)
        return 1
    if not process_moved_normally:
        print("FAIL: process corner had no measurable effect even without sabotage — "
              "the corner sweep may not be taking effect at all", file=sys.stderr)
        return 1
    if process_moved_sabotaged:
        print("FAIL: sabotaged run still shows process-corner sensitivity — corner "
              "switching is not taking effect (every corner simulated as typical)",
              file=sys.stderr)
        return 1
    print("ok: corner switching demonstrably changes the simulated result")
    return 0


def _int_at_least(minimum: int):
    """argparse type: an integer >= minimum, else a usage error (exit 2)."""
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"invalid integer value: {text!r}") from None
        if value < minimum:
            raise argparse.ArgumentTypeError(f"must be >= {minimum} (got {value})")
        return value
    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sim-harness", description=__doc__)
    parser.add_argument("--version", action="version", version=f"sim-harness-core {HARNESS_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list experiments with a testbench")
    p_list.set_defaults(func=cmd_list)

    p_run = sub.add_parser("run", help="run a testbench's PVT grid")
    p_run.add_argument("experiment", help="sim/<experiment-slug>/ (or its testbench/ dir)")
    p_run.add_argument("--corners", default=None,
                       help="comma-separated corner or corner-set names; overrides the "
                            "manifest's 'corners' (default: the manifest's selection)")
    p_run.add_argument("--jobs", type=_int_at_least(1), default=1)
    p_run.add_argument("--num-threads", type=_int_at_least(0), default=0)
    p_run.add_argument("--workdir", default="",
                       help="parent directory for this run's own scratch workspace "
                            "(default: sim/<slug>/_build/)")
    p_run.add_argument("--claim", default="")
    p_run.add_argument("--supersedes", default="")
    p_run.add_argument("--subset-reason", default="",
                       help="why this recorded run covers only part of the mandated PVT "
                            "matrix; required (nonblank) for a recorded subset, recorded "
                            "verbatim beside the subset warning; not needed for a "
                            "full-matrix, --no-write or --sabotage run")
    p_run.add_argument("--no-write", action="store_true", help="simulate but write no record")
    p_run.add_argument("--sabotage", action="store_true",
                        help="force every corner to typical (implies --no-write)")
    p_run.add_argument("--allow-toolchain-drift", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_selftest = sub.add_parser("selftest", help="negative-control corner-switching self-test")
    p_selftest.add_argument("experiment")
    p_selftest.add_argument("--corners", default=None,
                            help="comma-separated corner or corner-set names; overrides the "
                                 "manifest's 'corners' (default: the manifest's selection)")
    p_selftest.add_argument("--jobs", type=_int_at_least(1), default=1)
    p_selftest.add_argument("--workdir", default="",
                            help="parent directory for this selftest's own scratch "
                                 "workspace (default: sim/<slug>/_selftest/)")
    p_selftest.add_argument("--allow-toolchain-drift", action="store_true",
                            help="proceed despite toolchain drift (printed as a warning; "
                                 "malformed pins still fail)")
    p_selftest.set_defaults(func=cmd_selftest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
