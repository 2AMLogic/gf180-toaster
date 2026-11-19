"""Summaries, spec checks, and append-only evidence records.

GENERIC CORE: record-id minting, grid summaries, per-axis sensitivity (the
guard against a corner sweep that silently pins one axis to typical),
spec-check evaluation, and an append-only Markdown writer. None of this
reads a block-specific field by name -- it works over ``tb.measure`` /
``tb.checks`` (both generic, see testbench.py) and
``evidence.BaseExtensions.render_lines()`` (block-extensible, see
evidence.py).

Record id shape: ``<YYYYMMDD>-<HHMMSS>-<short-git-sha>``. A block's own
CLAUDE.md / evidence convention should state that sim/ results are
append-only evidence; this module makes that mechanical rather than a
manual promise: it never overwrites an existing record, minting a new
(still conforming) record-id on collision instead, and a correction is
expected to reference the prior record via ``supersedes`` rather than
editing it in place. A recorded run reserves its id *before* simulating
(:func:`reserve_record_id`, an atomic ``mkdir`` of ``corners/<record-id>/``)
so its raw logs and composed decks land in a bundle no other run can share.

EXTENSION POINT: :func:`build_record` bundles ``evidence.as_dict()``
untouched into the record, so a block's replacement ``BaseExtensions``
subclass (see evidence.py) needs no change here. A block wanting additional
*record-level* sections beyond what is here (e.g. a bespoke plot or extra
provenance block) should post-process the dict :func:`build_record` returns
before calling :func:`render_markdown`, or fork that one function -- the
minting/summary/check machinery above it stays shared.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import hashlib
import math
import platform
import re
import socket
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from . import HARNESS_VERSION, UPSTREAM_PATTERN
from .corners import DEFAULT_SUPPLY_TOLERANCE, DEFAULT_TEMPERATURES_C
from .evidence import BaseExtensions
from .pdk import Pdk
from .runner import PointResult
from .testbench import AXES, Testbench

#: Subdirectories of ``sim/<experiment-slug>/`` this module writes into.
SNAPSHOT_DIR = "netlist-snapshots"
RECORDS_DIR = "records"
#: Per-record evidence bundles: ``corners/<record-id>/`` holds each point's
#: raw simulator log and composed deck.
EVIDENCE_DIR = "corners"
#: Present in a bundle until its record is written; see reserve_record_id.
INCOMPLETE_MARKER = "INCOMPLETE"

#: Minimum number of distinct process corners for the matrix to count as
#: "full" without a written justification.
MIN_PROCESS_CORNERS = 3

#: Maximum number of absent tuples listed in coverage metadata; the exact
#: total is always reported separately.
COVERAGE_SAMPLE_LIMIT = 5

_AXIS_GROUP_KEY = {
    "process": lambda p: (p["temp_c"], p["vdd"]),
    "temperature": lambda p: (p["corner"], p["vdd"]),
    "supply": lambda p: (p["corner"], p["temp_c"]),
}
#: Names of the fixed axes (in key order) for each swept axis's slices.
_AXIS_FIXED = {
    "process": ("temp_c", "vdd"),
    "temperature": ("corner", "vdd"),
    "supply": ("corner", "temp_c"),
}
_AXIS_LEVEL = {
    "process": lambda p: p["corner"],
    "temperature": lambda p: p["temp_c"],
    "supply": lambda p: p["vdd"],
}


def _git(*args: str, cwd: Path) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
        )
        return out.stdout.strip()
    except OSError:  # pragma: no cover - git always present in a scaffolded repo
        return ""


def git_provenance(repo_root: Path) -> dict:
    commit = _git("rev-parse", "HEAD", cwd=repo_root)
    dirty = bool(_git("status", "--porcelain", cwd=repo_root))
    return {
        "commit": commit or "unknown",
        "short": (commit[:7] if commit else "unknown"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_root) or "unknown",
        "dirty": dirty,
    }


def format_record_id(short_sha: str, when: _dt.datetime) -> str:
    """``<YYYYMMDD>-<HHMMSS>-<short-git-sha>``."""
    return f"{when.strftime('%Y%m%d-%H%M%S')}-{short_sha}"


#: Shape of a record id as :func:`format_record_id` mints it. The sha part is
#: the 7-char short sha from :func:`git_provenance`, or the literal
#: ``unknown`` when git is unavailable. Anchored, with no path separators or
#: dots, so a value that matches can be joined onto a path safely.
RECORD_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9A-Za-z]+$")


def record_exists(experiment_dir: Path, record_id: str) -> bool:
    """Does ``record_id`` name a finished Markdown record in this experiment?

    For validating a reference to a prior record (``supersedes``), not for
    reservation: unlike :func:`_id_taken`, a netlist snapshot or an evidence
    bundle alone does not count -- only ``records/<record-id>.md`` as a
    regular file, which :func:`write_record` creates exclusively with its
    full text. A value not matching :data:`RECORD_ID_RE` is rejected before
    any path is built from it. Record contents are not parsed.
    """
    if not RECORD_ID_RE.fullmatch(record_id):  # fullmatch: no trailing "\n"
        return False
    return (experiment_dir / RECORDS_DIR / f"{record_id}.md").is_file()


def _id_taken(experiment_dir: Path, record_id: str) -> bool:
    return (experiment_dir / RECORDS_DIR / f"{record_id}.md").exists() or (
        experiment_dir / SNAPSHOT_DIR / f"{record_id}.spice"
    ).exists()


def reserve_record_id(
    repo_root: Path,
    experiment_dir: Path,
    when: _dt.datetime | None = None,
    git: dict | None = None,
) -> tuple[str, Path]:
    """Exclusively reserve a fresh ``<record-id>`` and its evidence bundle.

    Ownership is claimed by atomically creating
    ``<experiment_dir>/corners/<record-id>/`` (``mkdir`` without
    ``exist_ok``): of any number of concurrent callers, exactly one creates a
    given directory, and the others advance the timestamp and try the next
    id. The id shape stays ``<YYYYMMDD>-<HHMMSS>-<short-git-sha>``.

    The new bundle starts with an :data:`INCOMPLETE_MARKER` file; it is only
    removed by :func:`finalize_bundle` once the record has been written, so
    an interrupted or failed run leaves its diagnostics in place, explicitly
    marked as not a finished record. Returns ``(record_id, bundle_dir)``.
    """
    when = when or _dt.datetime.now(_dt.timezone.utc)
    short_sha = (git or git_provenance(repo_root))["short"]
    evidence_root = experiment_dir / EVIDENCE_DIR
    evidence_root.mkdir(parents=True, exist_ok=True)
    while True:
        record_id = format_record_id(short_sha, when)
        when += _dt.timedelta(seconds=1)
        if _id_taken(experiment_dir, record_id):
            continue
        bundle = evidence_root / record_id
        try:
            bundle.mkdir()
        except FileExistsError:
            continue
        (bundle / INCOMPLETE_MARKER).write_text(
            f"Evidence bundle reserved for record {record_id} at "
            f"{_dt.datetime.now(_dt.timezone.utc).isoformat()}.\n"
            "No finished record has been written for it; the files here are "
            "diagnostics only, not evidence.\n"
        )
        return record_id, bundle


def mark_incomplete(bundle_dir: Path, reason: str) -> None:
    """Record why a reserved bundle never became a finished record. Keeps
    every diagnostic already in the bundle; only appends to the marker."""
    marker = bundle_dir / INCOMPLETE_MARKER
    with marker.open("a") as fh:
        fh.write(f"{_dt.datetime.now(_dt.timezone.utc).isoformat()}: {reason}\n")


def finalize_bundle(bundle_dir: Path) -> None:
    """Drop the :data:`INCOMPLETE_MARKER` once the record has been written."""
    (bundle_dir / INCOMPLETE_MARKER).unlink(missing_ok=True)


def summarize(results: list[PointResult], measure_names: list[str]) -> dict:
    """Min / max / mean / spread of each measurement across the PVT grid."""
    summary: dict[str, dict] = {}
    ok = [r for r in results if r.status == "ok"]
    for name in measure_names:
        samples = [(r.measurements[name], r.point.corner_id) for r in ok if name in r.measurements]
        if not samples:
            summary[name] = {"n": 0}
            continue
        values = [v for v, _ in samples]
        lo_value, lo_at = min(samples, key=lambda s: s[0])
        hi_value, hi_at = max(samples, key=lambda s: s[0])
        mean = sum(values) / len(values)
        spread_pct = (hi_value - lo_value) / abs(mean) * 100.0 if mean else None
        summary[name] = {
            "n": len(values),
            "min": lo_value,
            "min_at": lo_at,
            "max": hi_value,
            "max_at": hi_at,
            "mean": mean,
            "spread_pct": spread_pct,
        }
    return summary


def _spread_pct(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    if not mean:
        return None
    return (max(values) - min(values)) / abs(mean) * 100.0


def axis_sensitivity(results: list[PointResult], measure_names: list[str]) -> dict:
    """Per-axis spread: does *each* PVT axis move each measurement?

    For axis ``a``, the grid is sliced into groups in which the other two
    axes are held fixed, so the only thing varying inside a group is ``a``.
    ``min_pct`` is the weakest slice (what a ``min_spread_pct_by_axis`` floor
    is checked against, so a single stuck slice cannot hide behind the
    others); ``max_pct`` is the strongest. Swept slices whose relative spread
    is undefined (zero mean) are listed in ``undefined_slices`` and make any
    configured per-axis check fail -- they are never dropped or read as 0.
    Fewer than two distinct levels
    means the axis was never swept -- reported as unverifiable, not a pass.
    """
    ok = [r for r in results if r.status == "ok"]
    points = [(r.point.as_dict(), r.measurements) for r in ok]

    out: dict[str, dict] = {}
    for name in measure_names:
        per_axis: dict[str, dict] = {}
        rows = [(p, m[name]) for p, m in points if name in m]
        for axis in AXES:
            levels = {_AXIS_LEVEL[axis](p) for p, _ in rows}
            groups: dict[tuple, list[float]] = defaultdict(list)
            for point, value in rows:
                groups[_AXIS_GROUP_KEY[axis](point)].append(value)
            spreads: list[float] = []
            undefined: list[dict] = []
            for key, values in groups.items():
                spread = _spread_pct(values)
                if spread is not None:
                    spreads.append(spread)
                elif len(values) >= 2:
                    # Swept slice whose relative spread is undefined (zero
                    # mean). Never drop it or call it 0: keep it so the
                    # per-axis checks can fail on it by name.
                    undefined.append(
                        {
                            "fixed": dict(zip(_AXIS_FIXED[axis], key)),
                            "reason": "zero mean -- relative spread undefined",
                            "values": list(values),
                        }
                    )
            per_axis[axis] = {
                "levels": len(levels),
                "groups": len(groups),
                "min_pct": min(spreads) if spreads else None,
                "max_pct": max(spreads) if spreads else None,
                "undefined_slices": undefined,
            }
        out[name] = per_axis
    return out


def evaluate_checks(
    checks: dict[str, dict],
    results: list[PointResult],
    summary: dict,
    sensitivity: dict | None = None,
    allow_unswept_axes: bool = False,
) -> list[dict]:
    """Return a list of check failures (empty list == everything passed).

    ``allow_unswept_axes`` downgrades "this axis was never swept, so its
    sensitivity is unverifiable" from a failure to a silent skip -- only set
    for runs already declared non-evidence (``--no-write``) or that carry a
    written subset justification.
    """
    sensitivity = sensitivity or {}
    failures: list[dict] = []

    def _bad(x) -> bool:
        # NaN makes every comparison False, i.e. a silent pass -- refuse it.
        return x is not None and (
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
        )

    def _nonfinite(name, kind, limit, value, at, **extra):
        failures.append(
            {"measurement": name, "kind": kind, **extra, "limit": limit, "value": value,
             "at": at, "note": "nonfinite limit or value -- cannot compare"}
        )

    for name, spec in checks.items():
        low = spec.get("min")
        high = spec.get("max")
        if low is not None or high is not None:
            for result in results:
                if result.status != "ok" or name not in result.measurements:
                    continue
                value = result.measurements[name]
                if _bad(value) or _bad(low) or _bad(high):
                    _nonfinite(name, "min/max", {"min": low, "max": high}, value,
                               result.point.corner_id)
                    continue
                if low is not None and value < low:
                    failures.append(
                        {"measurement": name, "kind": "min", "limit": low, "value": value,
                         "at": result.point.corner_id}
                    )
                if high is not None and value > high:
                    failures.append(
                        {"measurement": name, "kind": "max", "limit": high, "value": value,
                         "at": result.point.corner_id}
                    )
        for kind, limit in (
            ("max_spread_pct", spec.get("max_spread_pct")),
            ("min_spread_pct", spec.get("min_spread_pct")),
        ):
            if limit is None:
                continue
            observed = (summary.get(name) or {}).get("spread_pct")
            if _bad(limit) or _bad(observed):
                _nonfinite(name, kind, limit, observed, "grid")
                continue
            violated = (
                observed is None
                or (kind == "max_spread_pct" and observed > limit)
                or (kind == "min_spread_pct" and observed < limit)
            )
            if violated:
                failures.append(
                    {"measurement": name, "kind": kind, "limit": limit, "value": observed, "at": "grid"}
                )
        for kind, key, reduce_key in (
            ("min_spread_pct_by_axis", "min_spread_pct_by_axis", "min_pct"),
            ("max_spread_pct_by_axis", "max_spread_pct_by_axis", "max_pct"),
        ):
            for axis, limit in (spec.get(key) or {}).items():
                stats = (sensitivity.get(name) or {}).get(axis) or {}
                if stats.get("levels", 0) < 2:
                    if allow_unswept_axes:
                        continue
                    failures.append(
                        {"measurement": name, "kind": kind, "axis": axis, "limit": limit,
                         "value": None, "at": f"axis:{axis}",
                         "note": "axis was never swept — sensitivity unverifiable"}
                    )
                    continue
                undefined = stats.get("undefined_slices") or []
                if undefined and not _bad(limit):
                    for sl in undefined:
                        failures.append(
                            {"measurement": name, "kind": kind, "axis": axis, "limit": limit,
                             "value": None, "at": f"axis:{axis}", "fixed": sl["fixed"],
                             "note": f"swept slice at fixed {sl['fixed']} has undefined "
                                     f"relative spread ({sl['reason']}) -- cannot verify"}
                        )
                    continue
                observed = stats.get(reduce_key)
                if _bad(limit) or _bad(observed):
                    _nonfinite(name, kind, limit, observed, f"axis:{axis}", axis=axis)
                    continue
                violated = (
                    observed is None
                    or (kind == "min_spread_pct_by_axis" and observed < limit)
                    or (kind == "max_spread_pct_by_axis" and observed > limit)
                )
                if violated:
                    failures.append(
                        {"measurement": name, "kind": kind, "axis": axis, "limit": limit,
                         "value": observed, "at": f"axis:{axis}"}
                    )
    return failures


def environment(
    pdk: Pdk,
    ngspice: str,
    repo_root: Path,
    git: dict | None = None,
    toolchain: dict | None = None,
) -> dict:
    """Reproducibility provenance for the record.

    ``git`` should be sampled *before* the run starts -- the harness writes
    its own per-corner logs into the tracked evidence tree, so sampling
    afterwards would report every record as taken against a dirty tree.
    """
    try:
        user = getpass.getuser()
    except Exception:  # pragma: no cover - unusual environments
        user = "unknown"
    return {
        "harness_version": HARNESS_VERSION,
        "harness_upstream": UPSTREAM_PATTERN,
        "ngspice": ngspice,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "host": socket.gethostname(),
        "user": user,
        "pdk": pdk.provenance(),
        "git": git if git is not None else git_provenance(repo_root),
        "toolchain": toolchain or {},
    }


def _tuple_coverage(tuples: set, expected: set) -> dict:
    """Coverage of ``expected`` (process, temp, vdd) tuples by ``tuples``.

    Counts are over unique normalized tuples, so repeats never fill holes.
    ``absent_samples`` is the first COVERAGE_SAMPLE_LIMIT absent tuples in
    sorted order; ``absent`` is the exact total.
    """
    absent = sorted(expected - tuples)
    return {
        "complete": not absent,
        "expected": len(expected),
        "observed": len(tuples & expected),
        "absent": len(absent),
        "absent_samples": [
            {"corner": c, "temp_c": t, "vdd": v}
            for c, t, v in absent[:COVERAGE_SAMPLE_LIMIT]
        ],
    }


def _fmt_tuple(sample: dict) -> str:
    return f"{sample['corner']}/{sample['temp_c']:g} C/{sample['vdd']:.2f} V"


def _coverage_from_record(record: dict) -> dict | None:
    """Coverage for a record: stored metadata, else derived from retained
    point coordinates (observed-axis product only), else None (unknown).
    ``matrix.full`` and raw counts are never used to infer completeness."""
    stored = (record.get("matrix") or {}).get("coverage")
    if stored is not None:
        return stored
    pts = record.get("points") or []
    try:
        tuples = {
            (p["corner"], round(float(p["temp_c"]), 6), round(float(p["vdd"]), 6))
            for p in pts
        }
    except (KeyError, TypeError, ValueError):
        return None
    if not tuples:
        return None
    expected = {
        (c, t, v)
        for c in {x[0] for x in tuples}
        for t in {x[1] for x in tuples}
        for v in {x[2] for x in tuples}
    }
    cov = _tuple_coverage(tuples, expected)
    cov["basis"] = "observed-axes"
    return cov


def _point_key(point) -> tuple:
    """Actual PVT identity (process, temperature, supply), never the
    rounded ``corner_id`` artifact name."""
    return (point.corner.name, round(point.temp_c, 6), round(point.vdd, 6))


def _key_samples(keys) -> list[dict]:
    return [
        {"corner": c, "temp_c": t, "vdd": v}
        for c, t, v in sorted(keys)[:COVERAGE_SAMPLE_LIMIT]
    ]


def reconcile_results(points, results) -> dict:
    """Compare the requested PVT points with the supplied results.

    Exactly one result per requested point is required. Reports missing
    requested points, duplicated result identities, and results for points
    that were never requested (each as an exact count plus the first
    COVERAGE_SAMPLE_LIMIT sorted samples). An empty requested grid is a
    problem of its own: there is nothing for a verdict to be about.
    ``complete`` is False whenever ``problems`` is nonempty.
    """
    requested = {_point_key(p) for p in points}
    counts: dict[tuple, int] = {}
    for r in results:
        key = _point_key(r.point)
        counts[key] = counts.get(key, 0) + 1
    missing = requested - set(counts)
    duplicate = {k for k, n in counts.items() if n > 1 and k in requested}
    unexpected = set(counts) - requested
    problems: list[str] = []
    if not requested:
        problems.append("empty requested grid: no PVT points were requested")
    if missing:
        problems.append(f"{len(missing)} requested point(s) have no result")
    if duplicate:
        problems.append(f"{len(duplicate)} requested point(s) have more than one result")
    if unexpected:
        problems.append(f"{len(unexpected)} result identity(ies) were never requested")
    return {
        "complete": not problems,
        "requested": len(requested),
        "results": len(results),
        "missing": len(missing),
        "missing_samples": _key_samples(missing),
        "duplicate": len(duplicate),
        "duplicate_samples": _key_samples(duplicate),
        "unexpected": len(unexpected),
        "unexpected_samples": _key_samples(unexpected),
        "problems": problems,
    }


def matrix_conformance(tb: Testbench, points) -> dict:
    """Is this run the full PVT matrix, or a subset?

    A block's evidence convention should require the "corner matrix run"
    field to state the full matrix unless the record states why a subset was
    used; this returns what is missing so the caller can insist on a written
    justification instead of quietly recording a thinner run.
    """
    temps = {round(p.temp_c, 6) for p in points}
    supplies = {round(p.vdd, 6) for p in points}
    process = {p.corner.name for p in points}

    required_temps = {round(t, 6) for t in DEFAULT_TEMPERATURES_C}
    nominal = tb.nominal_supply_v
    required_supplies = {
        round(nominal * (1.0 - DEFAULT_SUPPLY_TOLERANCE), 6),
        round(nominal, 6),
        round(nominal * (1.0 + DEFAULT_SUPPLY_TOLERANCE), 6),
    }

    missing: list[str] = []
    if not required_temps <= temps:
        missing.append(
            "temperature: missing " + ", ".join(f"{t:g} C" for t in sorted(required_temps - temps))
        )
    if not required_supplies <= supplies:
        missing.append(
            "supply: missing " + ", ".join(f"{v:.2f} V" for v in sorted(required_supplies - supplies))
        )
    if len(process) < MIN_PROCESS_CORNERS:
        missing.append(
            f"process: only {len(process)} corner(s) ({', '.join(sorted(process))}); "
            f"at least {MIN_PROCESS_CORNERS} expected"
        )

    # Tuple coverage: every observed process must appear at every required
    # temperature/supply, and the observed axes must form a full Cartesian
    # product (holes involving extra temperatures/supplies count too).
    tuples = {(p.corner.name, round(p.temp_c, 6), round(p.vdd, 6)) for p in points}
    expected = {
        (c, t, v)
        for c in process
        for t in (required_temps | temps)
        for v in (required_supplies | supplies)
    }
    coverage = _tuple_coverage(tuples, expected)
    if coverage["absent"]:
        shown = ", ".join(_fmt_tuple(x) for x in coverage["absent_samples"])
        more = coverage["absent"] - len(coverage["absent_samples"])
        missing.append(
            f"tuples: {coverage['absent']} of {coverage['expected']} "
            f"process/temperature/supply combinations absent (e.g. {shown}"
            + (f", and {more} more" if more > 0 else "") + ")"
        )
    return {"full": not missing, "missing": missing, "coverage": coverage}


def build_record(
    tb: Testbench,
    pdk: Pdk,
    points,
    results: list[PointResult],
    ngspice: str,
    repo_root: Path,
    record_id: str,
    started_utc: str,
    wall_seconds: float,
    claim: str = "",
    supersedes: str = "",
    statistical_convention: str = "",
    subset_reason: str = "",
    git: dict | None = None,
    extensions: BaseExtensions | None = None,
    allow_unswept_axes: bool = False,
    toolchain: dict | None = None,
    evidence_dir: Path | None = None,
) -> dict:
    """Assemble the record dict.

    ``evidence_dir`` is the record's reserved bundle (see
    :func:`reserve_record_id`); when given, the record names it and every
    point's ``deck`` / ``log`` is an experiment-relative path into it.
    """
    measure_names = list(tb.measure)
    summary = summarize(results, measure_names)
    sensitivity = axis_sensitivity(results, measure_names)
    failures = evaluate_checks(
        tb.checks, results, summary, sensitivity, allow_unswept_axes=allow_unswept_axes
    )
    n_ok = sum(1 for r in results if r.status == "ok")
    extensions = extensions if extensions is not None else tb.evidence

    # Reconcile before the verdict: limit checks skip absent points, so a
    # short/duplicated/foreign result list would otherwise pass vacuously.
    reconciliation = reconcile_results(points, results)

    if not reconciliation["complete"] or n_ok != len(results):
        status = "error"
    elif failures:
        status = "fail"
    elif extensions.is_characterization:
        # Measured values with provenance, not a verified specification.
        status = "characterized"
    else:
        status = "pass"

    corners = []
    seen = set()
    for point in points:
        if point.corner.name not in seen:
            seen.add(point.corner.name)
            corners.append(
                {"name": point.corner.name, "sections": list(point.corner.sections),
                 "description": point.corner.description}
            )

    return {
        "record_id": record_id,
        "experiment": tb.experiment,
        "status": status,
        "started_utc": started_utc,
        "wall_seconds": round(wall_seconds, 2),
        "claim": claim or tb.claim,
        "supersedes": supersedes,
        "statistical_convention": statistical_convention,
        "subset_reason": subset_reason,
        "matrix": matrix_conformance(tb, points),
        "testbench": tb.provenance(),
        "environment": environment(pdk, ngspice, repo_root, git, toolchain),
        "evidence": extensions.as_dict(),
        "grid": {
            "corners": corners,
            "temperatures_c": sorted({p.temp_c for p in points}),
            "supplies_v": sorted({p.vdd for p in points}),
            "points": len(points),
            "points_ok": n_ok,
            "results": len(results),
        },
        "reconciliation": reconciliation,
        "measure": dict(tb.measure),
        "checks": {"spec": tb.checks, "passed": not failures, "failures": failures},
        "summary": summary,
        "sensitivity": sensitivity,
        "evidence_dir": (
            PointResult._ref("", evidence_dir, tb.experiment_dir) if evidence_dir else ""
        ),
        "points": [r.as_dict(base=tb.experiment_dir) for r in results],
        "_evidence_lines": extensions.render_lines(),
    }


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        if value != 0 and (abs(value) < 1e-3 or abs(value) >= 1e5):
            return f"{value:.6e}"
        return f"{value:.6g}"
    return str(value)


def describe_failure(failure: dict) -> str:
    axis = failure.get("axis")
    where = f" on the {axis} axis" if axis else ""
    note = failure.get("note")
    if note:
        return f"{failure['measurement']} {failure['kind']}{where}: {note}"
    return (
        f"{failure['measurement']} {failure['kind']}{where}="
        f"{_fmt(failure['limit'])} (got {_fmt(failure['value'])})"
    )


class RecordExists(RuntimeError):
    """Refused to overwrite an existing append-only record."""


def write_netlist_snapshot(tb: Testbench, experiment_dir: Path, record_id: str) -> Path:
    """Freeze the DUT netlist for this record, so a later edit under
    ``testbench/`` never changes what an existing record refers to."""
    out_dir = experiment_dir / SNAPSHOT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{record_id}.spice"
    if path.exists():
        raise RecordExists(f"{path} already exists; append-only evidence is never rewritten")
    # One buffer feeds both the header hash and the body, so they cannot
    # disagree; it is the captured fragment every point was simulated from.
    body = tb.captured_netlist()
    header = "\n".join(
        [
            f"* Frozen netlist snapshot for record {record_id}",
            f"* sha256     : {hashlib.sha256(body).hexdigest()}",
            "* This is a verbatim copy taken at record time. Do not edit.",
            "",
        ]
    )
    try:
        with open(path, "xb") as fh:
            fh.write(header.encode("utf-8") + body)
    except FileExistsError as exc:
        raise RecordExists(
            f"{path} already exists; append-only evidence is never rewritten"
        ) from exc
    return path


def _coverage_line(record: dict) -> str:
    cov = _coverage_from_record(record)
    if cov is None:
        return "Tuple coverage unknown (record predates coverage metadata)."
    basis = (" over the recorded axes"
             if cov.get("basis") == "observed-axes" else "")
    if cov["complete"]:
        return (f"Full-factorial tuple coverage{basis} "
                f"(process x temperature x supply): all {cov['expected']} "
                "combinations present.")
    shown = ", ".join(_fmt_tuple(x) for x in cov["absent_samples"])
    return (f"**Sparse tuple coverage{basis}**: {cov['absent']} of "
            f"{cov['expected']} combinations absent (e.g. {shown}).")


def render_markdown(record: dict) -> str:
    """Render a built record as a Markdown evidence record.

    Deliberately plain: a block-specific evidence convention typically wants
    a more elaborate layout (see gf180-sar-adc's harness/report.py for one
    example, with a toolchain-drift banner and a rendered sensitivity
    table). Replace this function once that convention is ratified; the
    dict :func:`build_record` returns already carries everything it needs.
    """
    lines = [
        f"# {record['record_id']}",
        "",
        f"**Status**: {record['status']}",
        f"**Experiment**: {record['experiment']}",
        f"**Started (UTC)**: {record['started_utc']}",
        f"**Wall time**: {record['wall_seconds']}s",
    ]
    if record.get("supersedes"):
        lines.append(f"**Supersedes**: {record['supersedes']}")
    lines += ["", f"**Claim**: {record['claim'] or '(none stated)'}", ""]

    if record["statistical_convention"]:
        lines.append(f"- **Statistical convention**: {record['statistical_convention']}")
    grid = record["grid"]
    lines += [
        "- **Corner matrix run**:",
        "  - Process: " + ", ".join(c["name"] for c in grid["corners"]),
        "  - Temperature: " + ", ".join(f"{t:g} C" for t in grid["temperatures_c"]),
        "  - Supply: " + ", ".join(f"{v:.2f} V" for v in grid["supplies_v"]),
        f"  - Requested matrix coverage: {grid['points']} points requested",
        (f"  - Completed result coverage: {grid['points_ok']} of "
         f"{grid['points']} requested points completed"
         + (f" ({grid['results']} results supplied)" if "results" in grid else "")),
        "  - " + _coverage_line(record),
    ]
    if record["matrix"]["full"]:
        lines.append("  - Full PVT matrix.")
    else:
        lines.append("  - **Subset of the mandated PVT matrix.** Gaps: "
                     + "; ".join(record["matrix"]["missing"]) + ".")
        lines.append("  - Justification: " + (record["subset_reason"] or "(none given)"))

    tbp = record.get("testbench") or {}
    if tbp.get("manifest_sha256") or tbp.get("netlist_sha256"):
        lines += ["", "## Input provenance"]
        if tbp.get("netlist_provenance"):
            lines.append(f"- **Netlist provenance**: {tbp['netlist_provenance']}")
        if tbp.get("manifest_sha256"):
            lines.append(f"- **Manifest SHA-256**: `{tbp['manifest_sha256']}`")
        if tbp.get("netlist_sha256"):
            lines.append(f"- **Netlist fragment SHA-256**: `{tbp['netlist_sha256']}`")
        if tbp.get("frozen_manifest") and tbp.get("frozen_netlist"):
            lines.append(f"- **Retained manifest**: `{tbp['frozen_manifest']}`")
            lines.append(f"- **Retained netlist fragment**: `{tbp['frozen_netlist']}`")

    lines += ["", "## Evidence"]
    lines += record.get("_evidence_lines", []) or ["- (no extension fields)"]

    lines += ["", "## Result"]
    # The overall verdict follows the record's own status (build_record), never
    # checks.passed alone: limit checks skip unsuccessful points, so an error
    # record can have an empty failure list.
    status = record.get("status") or ("pass" if record["checks"]["passed"] else "fail")
    failures = record["checks"]["failures"]
    if status == "error":
        bad = [p for p in record.get("points", []) if p.get("status") != "ok"]
        total = grid["points"]
        recon = record.get("reconciliation") or {}
        if bad or recon.get("complete", True):
            lines.append(
                f"**ERROR** — {len(bad) or total - grid['points_ok']} of {total} simulation "
                "points did not complete successfully; results are incomplete and no "
                "overall pass is claimed."
            )
        else:
            lines.append(
                "**ERROR** — supplied results do not match the requested grid; "
                "no overall pass is claimed."
            )
        if not recon.get("complete", True):
            lines += ["", "### Result/grid mismatch diagnostics",
                      f"- Requested {recon['requested']} unique points; "
                      f"{recon['results']} results supplied."]
            lines += [f"- {msg}" for msg in recon["problems"]]
            for label, key in (("Missing", "missing"), ("Duplicate", "duplicate"),
                               ("Unexpected", "unexpected")):
                if recon[key]:
                    shown = ", ".join(_fmt_tuple(x) for x in recon[key + "_samples"])
                    more = recon[key] - len(recon[key + "_samples"])
                    lines.append(f"- {label} ({recon[key]}): {shown}"
                                 + (f", and {more} more" if more > 0 else ""))
            lines.append("")
        for point in bad:
            line = f"- `{point['corner_id']}` {point['status']}"
            if point.get("message"):
                line += f": {point['message']}"
            lines.append(line)
        lines += ["", "### Limit checks (completed points only)"]
        if failures:
            lines += [f"- {describe_failure(f)}" for f in failures]
        else:
            lines.append("- No limit violations among completed points (not a pass: "
                         "unsuccessful points were not evaluated).")
    elif status == "characterized":
        lines.append(
            "**CHARACTERIZED** — all simulation points completed; values are measured "
            "characterization, not verification of a specification (see Summary)."
        )
    elif status == "pass":
        lines.append("**PASS** — all spec checks satisfied.")
    else:
        lines.append("**FAIL**")
        for failure in failures:
            lines.append(f"- {describe_failure(failure)}")

    if record.get("evidence_dir"):
        lines += [
            "",
            "## Raw evidence",
            f"Per-point raw simulator logs and composed decks, relative to "
            f"`sim/{record['experiment']}/`: `{record['evidence_dir']}/`",
            "",
        ]
        for point in record.get("points", []):
            line = (f"- `{point['corner_id']}` {point['status']}: "
                    f"log `{point['log']}`, deck `{point['deck']}`")
            if point.get("message"):
                line += f" — {point['message']}"
            lines.append(line)

    lines += ["", "## Summary"]
    for name, stats in record["summary"].items():
        if stats.get("n", 0) == 0:
            lines.append(f"- `{name}`: no successful measurements")
            continue
        lines.append(
            f"- `{name}`: min {_fmt(stats['min'])} (@{stats['min_at']}), "
            f"max {_fmt(stats['max'])} (@{stats['max_at']}), "
            f"mean {_fmt(stats['mean'])}, spread {_fmt(stats['spread_pct'])}%"
        )

    env = record["environment"]
    lines += [
        "",
        "## Environment",
        f"- Harness: v{env['harness_version']} (upstream pattern: {env['harness_upstream']})",
        f"- ngspice: {env['ngspice']}",
        (f"- PDK: {env['pdk'].get('name')} {env['pdk'].get('variant')} "
         f"({env['pdk'].get('version')}, via {env['pdk'].get('discovered_via')})"),
        f"- Python: {env['python']} on {env['platform']}",
        f"- git: {env['git'].get('short')} on {env['git'].get('branch')}"
        + (" (dirty)" if env["git"].get("dirty") else ""),
    ]
    toolchain = env.get("toolchain") or {}
    if toolchain.get("pinned"):
        state = "satisfied" if toolchain.get("conforms") else "DRIFTED"
        lines.append(f"- Toolchain pins: **{state}**")
    lines.append("")
    return "\n".join(lines)


def write_record(record: dict, experiment_dir: Path) -> Path:
    """Append-only write of the Markdown record. Never overwrites."""
    records_dir = experiment_dir / RECORDS_DIR
    records_dir.mkdir(parents=True, exist_ok=True)
    path = records_dir / f"{record['record_id']}.md"
    if path.exists():
        raise RecordExists(f"{path} already exists; append-only evidence is never rewritten")
    text = render_markdown(record)
    # Exclusive create is the real protection: the exists() check above is
    # only a friendly early diagnostic and cannot stop a concurrent writer.
    try:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(text)
    except FileExistsError as exc:
        raise RecordExists(
            f"{path} already exists; append-only evidence is never rewritten"
        ) from exc
    return path
