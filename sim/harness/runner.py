"""Fragment-based ngspice deck composition and execution for one PVT point.

GENERIC CORE: :func:`compose_deck` assembles a complete, self-contained
ngspice deck from a testbench's netlist *fragment* (see testbench.py) plus
the PDK models and corner sections for one :class:`corners.PvtPoint`. Every
line the harness itself owns (models, corner ``.lib`` sections, ``.temp``,
the ``.control`` measurement block) is generated here; the fragment supplies
only the circuit. :func:`run_point` / :func:`run_grid` invoke ngspice on the
composed deck and parse its ``print`` output back into named measurements.

EXTENSION POINT -- ``extra_subckts``: a callable ``(pdk) -> list[str]`` a
block passes to :func:`compose_deck` to inject block-specific subckt
wrappers ahead of the testbench include -- gf180-sar-adc's harness uses
exactly this shape (there named ``mim_wrapper_subckts``) to bind a
PDK-variant-specific MIM capacitor model to a stable subckt name a CDAC
testbench can instantiate without hardcoding a metal-stack assumption. A
block with no such need passes nothing; the generic core never assumes one
exists.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable

from .corners import PvtPoint, check_unique_artifact_ids
from .pdk import Pdk
from .testbench import (
    _reserved_param_message,
    Testbench,
    reserved_param_collisions,
    validate_fragment_param_ownership,
)

NGSPICE = "ngspice"
DEFAULT_TIMEOUT_S = 300

# `print` output for a length-1 vector: "m_vout = 6.9043645202e-01"
_MEAS_RE = re.compile(r"^\s*m_(\w+)\s*=\s*([-+]?[0-9.]+(?:[eE][-+]?[0-9]+)?)\s*$")
# Fatal/error diagnostics. Ordinary "Warning ..." lines are deliberately NOT
# matched. `: no such command available` is what `-b` prints (still exiting 0)
# when the control block has an unknown command, i.e. the analysis never ran.
_ERROR_RE = re.compile(
    r"^\s*(?:Error|ERROR|Fatal|fatal error|doAnalyses:|.*: no such command available)",
    re.MULTILINE,
)

# Exit codes `ngspice -b` may return for a run to count as completed.
# Verified against ngspice-46: a clean batch run exits 0; a netlist/parse
# error ("Error: ...", "Simulation interrupted due to error!") exits 1; an
# explicit `quit N` in the control block exits N. Only 0 is success; there is
# no documented benign nonzero exit, so none is whitelisted.
ACCEPTED_EXIT_CODES = frozenset({0})

ExtraSubckts = Callable[[Pdk], list[str]]


def absolute_path(path: Path | str) -> Path:
    """Absolute form of ``path``, anchored at the caller's current directory.

    Lexically normalised (``..`` collapsed) but symlinks are not followed.
    Needed at the subprocess boundary: ngspice is spawned with ``cwd=workdir``,
    so a relative deck path in argv would be re-resolved against that cwd.
    """
    return Path(os.path.abspath(os.fspath(path)))


class NgspiceMissing(RuntimeError):
    pass


def ngspice_version() -> str:
    exe = shutil.which(NGSPICE)
    if not exe:
        raise NgspiceMissing(
            "ngspice not found on PATH.\n"
            "  macOS:  brew install ngspice\n"
            "  Debian: apt-get install ngspice\n"
        )
    out = subprocess.run(
        [exe, "--version"], capture_output=True, text=True, check=False
    ).stdout
    for line in out.splitlines():
        if "ngspice-" in line:
            return line.strip().lstrip("* ").strip()
    return out.strip().splitlines()[0] if out.strip() else "unknown"


def compose_deck(
    tb: Testbench,
    pdk: Pdk,
    point: PvtPoint,
    num_threads: int = 0,
    extra_subckts: ExtraSubckts | None = None,
) -> str:
    """Build the complete, self-contained ngspice deck for one PVT point.

    ``num_threads`` (0 = leave ngspice's default alone) emits ``set
    num_threads=N`` at the top of the control block. It is a scheduling
    knob, not a circuit one -- an OpenMP-built ngspice defaults to one
    thread per processor, which on a busy host makes several concurrent
    points fight over the same cores. Grid throughput comes from the
    caller's ``jobs`` (see :func:`run_grid`), not from ngspice's internal
    threads; ``num_threads=1`` is usually the faster setting on a
    contended host.

    ``extra_subckts`` -- see the module docstring's EXTENSION POINT.

    Composition refuses competing definitions of the harness-owned PVT
    parameters from either side: a ``params`` collision or a fragment
    top-level ``.param`` assignment to a reserved name (product#287)
    raises here as well as at manifest load, so a hand-built Testbench
    cannot bypass the validation.
    """
    for key in reserved_param_collisions(tb.params):
        raise ValueError(f"{tb.name}: {_reserved_param_message(key)}")
    # The fragment include follows the harness's own .param lines, so a
    # fragment-level assignment to a reserved name would compete with them;
    # checked here as well so a hand-built Testbench cannot bypass the
    # load-time validation.
    validate_fragment_param_ownership(tb)
    lines: list[str] = [
        f"* {tb.name} @ {point.corner_id} -- GENERATED by sim/harness, do not edit",
        f"* corner={point.corner.name} ({point.corner.description})",
        f"* temp={point.temp_c} C  vdd={point.vdd} V  pdk={pdk.variant}@{pdk.version}",
        "",
        "* ---- PVT parameters -------------------------------------------------",
        f".param vdd_nom={tb.nominal_supply_v!r}",
        f".param vdd_val={point.vdd!r}",
        f".param temp_c={point.temp_c!r}",
    ]
    for key, value in tb.params.items():
        lines.append(f".param {key}={value}")

    lines += ["", f"* ---- {pdk.name} models -----------------------------------------------"]
    if pdk.design_include is not None:
        lines.append(f'.include "{absolute_path(pdk.design_include)}"')
    for section in point.corner.sections:
        lines.append(f'.lib "{absolute_path(pdk.model_lib)}" {section}')

    if extra_subckts is not None:
        lines += ["", *extra_subckts(pdk)]

    lines += [
        "",
        f".temp {point.temp_c!r}",
    ]
    for option in tb.options:
        lines.append(f".options {option}")

    lines += [
        "",
        "* ---- testbench ------------------------------------------------------",
        f'.include "{absolute_path(tb.include_path)}"',
        "",
        "* ---- measurement ----------------------------------------------------",
        ".control",
        "set numdgt=10",
        "set noaskquit",
    ]
    if num_threads:
        lines.append(f"set num_threads={num_threads}")
    lines += [f"  {analysis}" for analysis in tb.analyses]
    for name, expr in tb.measure.items():
        lines.append(f"  let m_{name} = {expr}")
    for name in tb.measure:
        lines.append(f"  print m_{name}")
    lines += [".endc", ".end", ""]
    return "\n".join(lines)


@dataclass
class PointResult:
    point: PvtPoint
    status: str                                   # "ok" | "failed" | "error"
    measurements: dict[str, float] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    seconds: float = 0.0
    deck: str = ""
    log: str = ""
    message: str = ""
    #: Absolute locations of the files named by ``deck`` / ``log`` (which stay
    #: bare basenames for backward compatibility). Not serialised as-is;
    #: :meth:`as_dict` turns them into ``base``-relative references.
    deck_path: Path | None = field(default=None, repr=False, compare=False)
    log_path: Path | None = field(default=None, repr=False, compare=False)

    @staticmethod
    def _ref(name: str, path: Path | None, base: Path | None) -> str:
        """``path`` relative to ``base`` (POSIX form) when it lies under it,
        else the bare basename ``name`` -- never an absolute host path."""
        if base is None or path is None:
            return name
        try:
            return absolute_path(path).relative_to(absolute_path(base)).as_posix()
        except ValueError:
            return name

    def as_dict(self, base: Path | None = None) -> dict:
        """Serialisable form. ``base`` (normally the experiment directory)
        turns ``deck`` / ``log`` into references relative to it that still
        resolve after scratch space is cleared, rather than bare file names."""
        record = self.point.as_dict()
        record.update(
            {
                "status": self.status,
                "measurements": self.measurements,
                "seconds": round(self.seconds, 3),
                "deck": self._ref(self.deck, self.deck_path, base),
                "log": self._ref(self.log, self.log_path, base),
            }
        )
        if self.missing:
            record["missing_measurements"] = self.missing
        if self.message:
            record["message"] = self.message
        return record


def parse_measurements(text: str) -> dict[str, float]:
    found: dict[str, float] = {}
    for line in text.splitlines():
        match = _MEAS_RE.match(line)
        if match:
            try:
                found[match.group(1)] = float(match.group(2))
            except ValueError:  # pragma: no cover - regex already constrains this
                continue
    return found


def _timeout_stream(data) -> str:
    """Normalize a TimeoutExpired stream (bytes, str or None) to text."""
    if data is None:
        return ""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data).decode("utf-8", errors="replace")
    return str(data)


def run_point(
    tb: Testbench,
    pdk: Pdk,
    point: PvtPoint,
    workdir: Path,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    log_dir: Path | None = None,
    num_threads: int = 0,
    extra_subckts: ExtraSubckts | None = None,
) -> PointResult:
    """Simulate one PVT point. Never raises for simulation failure.

    ``workdir`` holds the generated deck (scratch, disposable) and is
    ngspice's cwd. ``log_dir`` -- when given -- is where the raw ngspice
    output lands as ``<corner-id>.log``; that is the
    ``sim/<slug>/corners/<record-id>/`` directory a block's evidence
    convention should use. When it differs from ``workdir`` the composed
    deck is also retained there as ``<corner-id>.spice`` (a netlist snapshot
    alone does not freeze the generated model sections, ``.temp``,
    parameters and control block), and the result's ``deck`` names that
    retained copy. ``log_dir`` defaults to ``workdir`` so a throwaway run
    does not touch the evidence tree.
    """
    workdir = absolute_path(workdir)
    log_dir = workdir if log_dir is None else absolute_path(log_dir)
    workdir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    deck_path = workdir / f"{point.corner_id}.spice"
    log_path = log_dir / f"{point.corner_id}.log"
    deck_text = compose_deck(
        tb, pdk, point, num_threads=num_threads, extra_subckts=extra_subckts
    )
    deck_path.write_text(deck_text)
    kept_deck = deck_path
    if log_dir != workdir:
        kept_deck = log_dir / deck_path.name
        kept_deck.write_text(deck_text)
    refs = {
        "deck": kept_deck.name, "log": log_path.name,
        "deck_path": kept_deck, "log_path": log_path,
    }

    started = time.monotonic()
    try:
        proc = subprocess.run(
            [NGSPICE, "-b", str(deck_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=workdir,
            check=False,
        )
        output = proc.stdout + "\n" + proc.stderr
        returncode = proc.returncode
    except FileNotFoundError as exc:
        raise NgspiceMissing(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        # Keep whatever the simulator emitted before it was killed; the
        # exception's streams may be bytes (even with text=True), str or None.
        partial = "".join(
            chunk if chunk.endswith("\n") else chunk + "\n"
            for chunk in (
                _timeout_stream(exc.stdout), _timeout_stream(exc.stderr)
            )
            if chunk
        )
        log_path.write_text(partial + f"TIMEOUT after {timeout_s}s\n")
        return PointResult(
            point=point,
            status="error",
            seconds=elapsed,
            **refs,
            message=f"ngspice timed out after {timeout_s}s",
        )
    elapsed = time.monotonic() - started
    log_path.write_text(output)

    measurements = parse_measurements(output)
    missing = [name for name in tb.measure if name not in measurements]
    first_error = next(
        (line.strip() for line in output.splitlines() if _ERROR_RE.match(line)), ""
    )
    bad_exit = returncode not in ACCEPTED_EXIT_CODES
    # float() accepts overflow such as "1e999" -> inf; a nonfinite measurement
    # is never a result. Kept in `measurements` (and the raw log) for debugging.
    nonfinite = [n for n, v in measurements.items() if not math.isfinite(v)]

    if missing or bad_exit or first_error or nonfinite:
        # Raw log and any parsed measurements stay available for debugging,
        # but the point is never "ok": complete measurements do not excuse a
        # failed process or a fatal diagnostic.
        if first_error:
            message = first_error
            if bad_exit:
                message = f"ngspice exit {returncode}: {first_error}"
        elif bad_exit:
            message = f"ngspice exit {returncode}"
            if missing:
                message += ", measurements missing: " + ", ".join(missing)
        else:
            message = f"ngspice exit {returncode}, no measurements parsed"
        if nonfinite and not (first_error or bad_exit):
            message = "nonfinite measurement(s): " + ", ".join(
                f"{n}={measurements[n]!r}" for n in nonfinite
            )
        elif nonfinite:
            message += "; nonfinite measurement(s): " + ", ".join(nonfinite)
        return PointResult(
            point=point,
            status="failed",
            measurements=measurements,
            missing=missing,
            seconds=elapsed,
            **refs,
            message=message,
        )

    return PointResult(
        point=point,
        status="ok",
        measurements=measurements,
        seconds=elapsed,
        **refs,
    )


def run_grid(
    tb: Testbench,
    pdk: Pdk,
    points: list[PvtPoint],
    workdir: Path,
    jobs: int = 1,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    on_result=None,
    log_dir: Path | None = None,
    num_threads: int = 0,
    extra_subckts: ExtraSubckts | None = None,
) -> list[PointResult]:
    """Run every PVT point; results come back in grid order regardless of jobs."""
    # Refuse before any worker starts: points sharing an id would overwrite
    # each other's deck/log in the shared workdir and evidence directory.
    check_unique_artifact_ids(points)
    # Resolve once, up front, so every worker agrees on the same directories.
    workdir = absolute_path(workdir)
    log_dir = None if log_dir is None else absolute_path(log_dir)
    results: list[PointResult | None] = [None] * len(points)

    def _one(index_point):
        index, point = index_point
        result = run_point(
            tb, pdk, point, workdir, timeout_s=timeout_s, log_dir=log_dir,
            num_threads=num_threads, extra_subckts=extra_subckts,
        )
        results[index] = result
        if on_result is not None:
            on_result(result)
        return result

    if jobs <= 1:
        for item in enumerate(points):
            _one(item)
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            list(pool.map(_one, enumerate(points)))

    return [r for r in results if r is not None]
