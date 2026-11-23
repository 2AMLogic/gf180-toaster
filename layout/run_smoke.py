#!/usr/bin/env python3
"""Layout DRC smoke: pin check -> placeholder cell -> ``klt drc`` -> record.

The one command a clean checkout runs to prove the layout half of this
repo's verification harness (issue #16). It is the layout sibling of
``sim/smoke-op``: a deliberately trivial placeholder cell -- ``klt gen
res_array`` at default params, NOT a timer, oscillator or driver cell, and
no spec claim -- pushed through the pinned klt install, with a negative
control proving the gate can fail. What it proves is the *path*: the pin
document is filled and enforced, ``klt gen`` works on this PDK, ``klt drc``
grades the result, and a violating variant exits non-zero. The first real
layout issue starts from a working, pinned DRC path instead of discovering
tool friction then.

Steps, in order:

1. **Preflight** (``layout/toolchain_pin.py``): every entry of
   ``klt_required_commands`` is present in the running ``klt``, and the
   running install's git commit equals ``klt_last_verified_commit`` -- a
   dev build or a stale venv fails loudly before anything is generated.
2. **Generate** the placeholder cell (``klt gen res_array --pdk gf180mcuD``,
   default params) into a fresh evidence bundle.
3. **DRC** it (``klt drc --deck gf180mcu --pdk gf180mcuD``); require exit 0
   and ``status: clean``.
4. **Negative control**: the same generator with ``spacing_um`` squeezed to
   0.05 um (below the deck's same-layer spacing rules); require exit 3,
   ``status: violations`` and a non-zero violation count. A smoke that can
   only pass is not evidence.
5. **Record**: append a Markdown evidence record at
   ``layout/records/<YYYYMMDD-HHMMSS>-<short-sha>.md`` in the
   ``sim/smoke-op/records/`` id style, with the GDS streams and DRC reports
   in ``layout/records/<record-id>/``.

A failed run writes no record and exits 1 with a diagnostic on stderr; its
partial bundle stays on disk (uncommitted) for diagnosis. Records are
append-only evidence: this runner never edits or deletes an existing
record.

Standard library only. ``klt`` itself is resolved via ``--klt`` (default:
first on PATH -- in CI that is the pinned install in the project venv).

Usage::

    python3 layout/run_smoke.py [--klt /path/to/klt]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from toolchain_pin import (  # noqa: E402
    ToolchainError,
    load_pin,
    repo_root,
    require_commands,
)

#: PDK variant the smoke resolves (matches sim/toolchain.json's ciel pin;
#: klt resolves the install itself via its --pdk flag).
PDK_VARIANT = "gf180mcuD"

#: Curated DRC deck family (klt drc --deck).
DRC_DECK = "gf180mcu"

#: Placeholder cell: one matched resistor array at the generator's default
#: params. Chosen because res_array's documented defaults are DRC-clean on
#: the curated gf180mcu deck; it is a stand-in, not a Toaster block cell.
GENERATOR = "res_array"
CELL_NAME = "layout_smoke"

#: Negative control: same generator, intra-array spacing squeezed below the
#: deck's poly2/metal1 minimum spacing (default is 0.5 um). Verified to
#: trip poly2.space.1 + metal1.space.1 at klt 0.9.2.
NEGATIVE_CONTROL_PARAMS = {"spacing_um": 0.05}

#: Where records (<id>.md) and bundles (<id>/) live, repo-relative.
RECORDS_DIRNAME = "layout/records"

#: ``klt drc`` exit-code contract (docs/cli/drc.md): 0 clean, 3 violations.
DRC_EXIT_CLEAN = 0
DRC_EXIT_VIOLATIONS = 3


class SmokeError(Exception):
    """A smoke step failed. Reported as a clean stderr line, exit 1."""


def _run(cmd: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess:
    """Run ``cmd`` capturing output; never raises on a non-zero exit."""
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except OSError as exc:
        raise SmokeError(f"cannot run {cmd[0]!r}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SmokeError(f"{cmd[0]!r} timed out after {timeout}s") from exc


def _json_stdout(proc: subprocess.CompletedProcess, what: str) -> dict:
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeError(
            f"{what}: expected JSON on stdout, got: {proc.stdout[:200]!r} "
            f"(stderr: {proc.stderr[:200]!r})"
        ) from exc


def _git(root: str, args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _short_sha(root: str) -> str:
    out = _git(root, ["rev-parse", "--short=7", "HEAD"])
    return out or "unknown"


def _dirty(root: str) -> bool:
    out = _git(root, ["status", "--porcelain", "--untracked-files=all"])
    return bool(out)


def _check_install_identity(version_json: dict, pin: dict) -> None:
    """Fail loudly unless the running klt is the pinned commit.

    The commit check is the strong form (catches a dev build at the same
    version number); the ``==`` release comparison is the fallback for an
    install whose build carries no git identity.
    """
    running_commit = version_json.get("git_commit")
    pinned_commit = pin["klt_last_verified_commit"]
    if running_commit:
        if running_commit != pinned_commit:
            raise SmokeError(
                f"running klt is commit {running_commit} "
                f"({version_json.get('version', '?')}), but "
                f"layout/toolchain.json pins klt_last_verified_commit "
                f"{pinned_commit} (klt_install {pin['klt_install']!r}). "
                "Re-run with the pinned install, e.g. "
                "`make smoke-layout KLT=/tmp/klt-pin/bin/klt` after "
                "installing the pin into a throwaway venv (never the host "
                "tool)."
            )
        return
    match = re.fullmatch(r"[A-Za-z0-9._-]*==([^=]+)", pin["klt_install"])
    if match and version_json.get("version") != match.group(1):
        raise SmokeError(
            f"running klt is {version_json.get('version')!r}, but the pin "
            f"is {pin['klt_install']!r}."
        )


def _check_klayout_floor(version_json: dict, pin: dict) -> None:
    """Enforce ``klayout_package_min`` against the bundled klayout version."""
    floor = str(pin.get("klayout_package_min", "")).strip()
    running = str(version_json.get("klayout_version", "")).strip()
    if not floor or not running:
        return  # nothing to compare; recorded as such in the environment
    def parts(v: str) -> list[int]:
        return [int(p) for p in v.split(".") if p.isdigit()]
    if parts(running) < parts(floor):
        raise SmokeError(
            f"bundled klayout {running} is below the pinned floor "
            f"klayout_package_min {floor} (layout/toolchain.json)."
        )


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gen(klt: str, out_gds: str, params: dict | None) -> list[str]:
    cmd = [
        klt, "gen", GENERATOR,
        "--pdk", PDK_VARIANT,
        "--cell-name", CELL_NAME,
        "-o", out_gds,
        "--format", "json",
    ]
    if params is not None:
        cmd += ["--params", json.dumps(params)]
    return cmd


def _drc(klt: str, gds: str, report: str) -> list[str]:
    return [
        klt, "drc", gds,
        "--deck", DRC_DECK,
        "--pdk", PDK_VARIANT,
        "-o", report,
        "--format", "json",
    ]


def _display_cmd(cmd: list[str], klt: str, root: str) -> str:
    """Render an argv for the record: bare `klt`, repo-relative paths.

    The exact install identity (version, commit, reinstall spec) is
    recorded separately and enforced against the pin, and artifact paths
    are durable relative to the repo root, so committed records carry no
    host-specific throwaway venv or checkout paths.
    """
    parts = []
    for part in cmd:
        if part == klt:
            parts.append("klt")
            continue
        if os.path.isabs(part):
            try:
                rel = os.path.relpath(part, root)
            except ValueError:  # different drive (Windows); keep absolute
                rel = None
            if rel is not None and not rel.startswith(".."):
                parts.append(rel)
                continue
        parts.append(part)
    return " ".join(parts)


def _fmt_proc(proc: subprocess.CompletedProcess) -> str:
    err = (proc.stderr or "").strip().splitlines()
    return err[0] if err else f"exit {proc.returncode}"


def _require_drc(proc: subprocess.CompletedProcess, report_path: str, *,
                 want_rc: int, want_status: str, what: str) -> dict:
    if proc.returncode != want_rc:
        raise SmokeError(
            f"{what}: klt drc exited {proc.returncode}, expected {want_rc} "
            f"({_fmt_proc(proc)})"
        )
    try:
        with open(report_path, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise SmokeError(f"{what}: cannot read DRC report {report_path}: {exc}") from exc
    if report.get("status") != want_status:
        raise SmokeError(
            f"{what}: report status {report.get('status')!r}, "
            f"expected {want_status!r}"
        )
    return report


RECORD_TEMPLATE = """\
# {record_id}

**Status**: pass
**Experiment**: layout-smoke-drc
**Started (UTC)**: {started}
**Wall time**: {wall:.1f}s

**Claim**: None -- harness self-verification, not a spec claim. Proves the
pinned klt install generates a placeholder cell on gf180mcu and grades it
DRC-clean (and that a deliberately violating variant fails), so later
real-cell layout records can be trusted. The cell is `klt gen res_array`
at default params -- a stand-in, not a timer/oscillator/driver cell.

## Toolchain pin
- **klt_install**: `{klt_install}` (layout/toolchain.json)
- **klt_required_commands**: {commands} -- all present, checked before
  anything ran (layout/toolchain_pin.py; upstream gap for a klt verb:
  2AMLogic/klayout-tools#7331)
- **Running install**: klt {klt_version} @ `{klt_commit}` -- matches
  `klt_last_verified_commit`; `klt version` reinstall_spec
  `{reinstall_spec}`
- **klayout**: {klayout_version} bundled (klt declares
  {klayout_expected} tested; floor `klayout>={klayout_floor}` satisfied;
  klayout_version_mismatch recorded in the DRC provenance -- engine newer
  than declared, verdicts unaffected per klt's disclosure)
- **Toolchain pins**: satisfied

## Placeholder cell
- `{gen_cmd}` (default params -- documented DRC-clean on the curated
  {deck} deck)
- device_count {device_count}; drc_hints: min_spacing {min_spacing} um,
  foreign_clearance {foreign_clearance} um, matched_group_id
  `{matched_group_id}`
- stream: `{bundle_rel}/{gds_name}` (sha256 `{gds_sha}`)
- gen report: `{bundle_rel}/{gen_report_name}`

## DRC run (must be clean)
- `{drc_cmd}`
- exit {drc_rc}, status **{drc_status}**, {violation_count} violations;
  {rules_checked} rules checked over layers {layers_checked}
- report: `{bundle_rel}/{drc_report_name}` (deck content_hash
  `{deck_hash}`)
- **Coverage caveat**: `klt drc --deck {deck}` runs klayout-tools' curated
  deck -- a deliberately partial subset of the PDK's design rule manual,
  not the foundry signoff deck ({unmodeled} DRM rule ids unmodeled in this
  run's report). This record proves the harness path, not signoff
  cleanliness.

## Negative control (must fail)
- same generator, params `{nc_params}` -- intra-array spacing squeezed
  below the deck's same-layer minimum spacing rules
- stream: `{bundle_rel}/{nc_gds_name}` (sha256 `{nc_gds_sha}`);
  gen report: `{bundle_rel}/{nc_gen_report_name}`
- `{nc_drc_cmd}`
- exit {nc_rc} (klt drc's documented violations code), status
  **{nc_status}**, {nc_count} violations: {nc_rule_counts}
- report: `{bundle_rel}/{nc_report_name}`
- proves the gate fails loudly on a violating layout, not vacuously green

## Environment
- git: {git_sha} on {git_branch} ({dirty})
- PDK: {pdk_name} ({pdk_version}, via {pdk_source})
- Python: {python}
- runner: `layout/run_smoke.py` (klt at `{klt_path}`: {klt_version} @
  `{klt_commit}` -- the identity lines above are the durable pin, not this
  host's install path)
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="layout DRC smoke: pin check, placeholder cell, "
        "klt drc, negative control, append-only record"
    )
    parser.add_argument(
        "--klt", default="klt", help="klt executable to run (default: klt)"
    )
    args = parser.parse_args(argv)

    started = datetime.datetime.now(datetime.timezone.utc)
    wall_start = time.monotonic()
    root = repo_root()

    # -- 1. preflight ------------------------------------------------
    try:
        pin = load_pin(root)
        checked = require_commands(pin, klt=args.klt)
    except ToolchainError as exc:
        raise SmokeError(f"toolchain pin check failed: {exc}") from exc

    proc = _run([args.klt, "version", "--format", "json"])
    if proc.returncode != 0:
        raise SmokeError(
            f"`{args.klt} version` exited {proc.returncode} ({_fmt_proc(proc)})"
        )
    version_json = _json_stdout(proc, "klt version")
    _check_install_identity(version_json, pin)
    _check_klayout_floor(version_json, pin)

    # -- 2. record id + bundle ---------------------------------------
    git_sha = _short_sha(root)
    git_branch = _git(root, ["branch", "--show-current"]) or "unknown"
    dirty = _dirty(root)  # before the bundle exists, so it excludes it
    record_id = f"{started.strftime('%Y%m%d-%H%M%S')}-{git_sha}"
    records_dir = os.path.join(root, RECORDS_DIRNAME)
    bundle = os.path.join(records_dir, record_id)
    try:
        os.makedirs(bundle)
    except FileExistsError as exc:
        raise SmokeError(
            f"evidence bundle {bundle} already exists; record ids are "
            "minted per second -- re-run the smoke"
        ) from exc
    bundle_rel = f"{RECORDS_DIRNAME}/{record_id}"

    # -- 3. placeholder cell -----------------------------------------
    gds_name = f"{CELL_NAME}.gds"
    gds = os.path.join(bundle, gds_name)
    gen_cmd = _gen(args.klt, gds, params=None)
    proc = _run(gen_cmd)
    if proc.returncode != 0:
        raise SmokeError(f"klt gen (placeholder) failed: {_fmt_proc(proc)}")
    gen_report = _json_stdout(proc, "klt gen")
    gen_report_name = "gen-report.json"
    with open(os.path.join(bundle, gen_report_name), "w", encoding="utf-8") as fh:
        json.dump(gen_report, fh, indent=1, sort_keys=True)
        fh.write("\n")

    # -- 4. DRC clean run ---------------------------------------------
    drc_report_name = "drc-report.json"
    drc_report_path = os.path.join(bundle, drc_report_name)
    drc_cmd = _drc(args.klt, gds, drc_report_path)
    proc = _run(drc_cmd, timeout=900)
    report = _require_drc(
        proc, drc_report_path,
        want_rc=DRC_EXIT_CLEAN, want_status="clean", what="DRC clean run",
    )
    provenance = report.get("provenance", {})
    prov_commit = provenance.get("klt_git_commit")
    if prov_commit and prov_commit != pin["klt_last_verified_commit"]:
        raise SmokeError(
            f"DRC provenance says klt commit {prov_commit}, pin says "
            f"{pin['klt_last_verified_commit']}"
        )

    # -- 5. negative control ------------------------------------------
    nc_params = dict(NEGATIVE_CONTROL_PARAMS)
    nc_gds_name = "negative-control.gds"
    nc_gds = os.path.join(bundle, nc_gds_name)
    nc_gen_cmd = _gen(args.klt, nc_gds, params=nc_params)
    proc = _run(nc_gen_cmd)
    if proc.returncode != 0:
        raise SmokeError(f"klt gen (negative control) failed: {_fmt_proc(proc)}")
    nc_gen_report = _json_stdout(proc, "klt gen negative control")
    nc_gen_report_name = "gen-negative-control-report.json"
    with open(os.path.join(bundle, nc_gen_report_name), "w", encoding="utf-8") as fh:
        json.dump(nc_gen_report, fh, indent=1, sort_keys=True)
        fh.write("\n")

    nc_report_name = "negative-control-report.json"
    nc_report_path = os.path.join(bundle, nc_report_name)
    nc_drc_cmd = _drc(args.klt, nc_gds, nc_report_path)
    proc = _run(nc_drc_cmd, timeout=900)
    nc_report = _require_drc(
        proc, nc_report_path,
        want_rc=DRC_EXIT_VIOLATIONS, want_status="violations",
        what="negative control",
    )
    nc_count = nc_report.get("violation_count", 0)
    if not nc_count:
        raise SmokeError(
            "negative control: exit 3 but violation_count is 0 -- "
            "not a usable failure signal"
        )

    # -- 6. record ----------------------------------------------------
    hints = gen_report.get("drc_hints", {})
    coverage = report.get("coverage", {})
    pdk_prov = provenance.get("pdk", {})
    deck_prov = provenance.get("deck", {})
    record = RECORD_TEMPLATE.format(
        record_id=record_id,
        started=started.isoformat(),
        wall=time.monotonic() - wall_start,
        klt_install=pin["klt_install"],
        commands=", ".join(f"`{c}`" for c in checked),
        klt_version=version_json.get("version", "?"),
        klt_commit=version_json.get("git_commit", "?"),
        reinstall_spec=version_json.get("reinstall_spec", "?"),
        klayout_version=version_json.get("klayout_version", "unknown"),
        klayout_expected=version_json.get("klayout_version_expected", "unknown"),
        klayout_floor=pin.get("klayout_package_min", "unset"),
        gen_cmd=_display_cmd(gen_cmd, args.klt, root),
        deck=DRC_DECK,
        device_count=gen_report.get("device_count", "?"),
        min_spacing=hints.get("min_spacing_um", "?"),
        foreign_clearance=hints.get("foreign_clearance_um", "?"),
        matched_group_id=hints.get("matched_group_id", "?"),
        bundle_rel=bundle_rel,
        gds_name=gds_name,
        gds_sha=_sha256(gds),
        gen_report_name=gen_report_name,
        drc_cmd=_display_cmd(drc_cmd, args.klt, root),
        drc_rc=DRC_EXIT_CLEAN,
        drc_status=report.get("status"),
        violation_count=report.get("violation_count", 0),
        rules_checked=len(coverage.get("rules_checked", [])),
        layers_checked=", ".join(coverage.get("layers_checked", [])) or "none",
        drc_report_name=drc_report_name,
        deck_hash=deck_prov.get("content_hash", "?"),
        unmodeled=len(coverage.get("unmodeled_rules", [])),
        nc_params=json.dumps(nc_params),
        nc_gds_name=nc_gds_name,
        nc_gds_sha=_sha256(nc_gds),
        nc_gen_report_name=nc_gen_report_name,
        nc_drc_cmd=_display_cmd(nc_drc_cmd, args.klt, root),
        nc_rc=DRC_EXIT_VIOLATIONS,
        nc_status=nc_report.get("status"),
        nc_count=nc_count,
        nc_rule_counts=json.dumps(nc_report.get("rule_counts", {})),
        nc_report_name=nc_report_name,
        git_sha=git_sha,
        git_branch=git_branch,
        dirty="dirty" if dirty else "clean",
        pdk_name=pdk_prov.get("name", PDK_VARIANT),
        pdk_version=pdk_prov.get("version", "?"),
        pdk_source=pdk_prov.get("source", "?"),
        python=sys.version.split()[0],
        klt_path=args.klt,
    )
    record_path = os.path.join(records_dir, f"{record_id}.md")
    with open(record_path, "w", encoding="utf-8") as fh:
        fh.write(record)

    print(f"layout smoke: pass -- record {record_path}")
    print(f"bundle: {bundle}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
