"""Testbench manifests.

GENERIC CORE. This module has no block-specific content: it is the manifest
schema (``tb.json``) and the netlist-fragment discipline every testbench
built on this harness follows, whatever the block.

Directory convention (a block adopts this by scaffolding its experiments
this way; the harness does not create it for you beyond ``sim/harness/``
itself):

    sim/<experiment-slug>/testbench/tb.json            the manifest (this module)
    sim/<experiment-slug>/testbench/<something>.spice  a *netlist fragment*

The fragment must NOT contain ``.include`` of models, ``.lib``, ``.temp``,
``.control`` or ``.end`` -- the harness owns all of those (see
runner.compose_deck) so that one netlist fragment can be swept across the
whole PVT grid without editing. The harness hands the fragment:

    vdd_val   the supply for this PVT point (nominal, +tol or -tol)
    vdd_nom   the nominal supply, for ratio-style measurements
    temp_c    the temperature for this PVT point (also set via .temp)

plus anything in the manifest's ``params`` map. Those three names are
RESERVED (:data:`RESERVED_PVT_PARAMS`, compared case-insensitively): a
``params`` key that collides with one is rejected at load time, and
``runner.compose_deck`` refuses a hand-built Testbench that does the same.
Select supply/temperature via ``nominal_supply_v`` / ``supply_tolerance`` /
``temperatures_c`` instead.

The same ownership extends to the fragment's ``.param`` directives, under a
scope rule rather than a ban. A *top-level* (global) assignment --
``.param vdd_val=1.0``, in any letter case, several assignments per
directive, or split across ``+`` continuation lines -- competes with the
definition the harness emits ahead of the fragment include, so it is
rejected by :func:`validate_netlist` at load time (before inputs are
frozen, evidence reserved or a worker launched) and again by
``runner.compose_deck`` (so a hand-built Testbench cannot compose competing
definitions either), with the fragment path and the offending line.
Everything else keeps working: ordinary custom parameters, *uses* of the
reserved names in expressions or comments (``.param ratio='vdd_val/2'``
reads the harness's value -- that is the intended use), and
*subcircuit-local* parameters, i.e. a ``.param`` inside a ``.subckt`` ...
``.ends`` body or a parameter on the ``.subckt`` line itself, which are
scoped to that subcircuit and do not touch the global PVT values.

Both checks recognize directives the way ngspice does (case-insensitive
prefix match, see :func:`directive_kind`), not by exact spelling: ``.inc``
is an ``.include``, ``.params`` is a ``.param``, and ``.macro`` / ``.eom``
open and close a subcircuit scope like ``.subckt`` / ``.ends``.

EXTENSION POINT: a manifest's ``evidence`` block is parsed by
``evidence.from_manifest`` -- see evidence.py for how a block extends the
fields accepted there once it has ratified its own evidence convention. The
"schematic" versus "extracted" netlist-provenance distinction tracks a
repo's own pre-layout (``design/``) versus post-layout (``layout/``) split;
the specific wording lives in :data:`PROVENANCE_RULE` below, generic and
unmodified.

NUMERIC FIELDS are validated at load time (before any simulator launch or
evidence reservation), and a violation names the manifest, the field and the
offending value:

    nominal_supply_v   finite number > 0
    supply_tolerance   finite number with 0 <= tol < 1 (fraction, 0.1 = +/-10%)
    temperatures_c     non-empty array of finite numbers
    measure            a JSON object mapping measurement names to ngspice
                       expressions; required and non-empty (a pair array or
                       any other non-object is refused rather than
                       dict()-coerced)
    measure.*          each expression a non-blank string, preserved verbatim
                       (expression *syntax* stays the simulator's -- there is
                       deliberately no ngspice expression parser here)
    checks             a JSON object when present; explicit null is invalid,
                       omitted means {}
    checks.*           every limit (min, max, *_spread_pct, and the values of
                       *_spread_pct_by_axis) is a finite number
    checks             a corner-matrix manifest must carry at least one
                       executable limit (a ``description`` or an empty
                       ``*_by_axis`` object is not one); measurement-only
                       experiments declare ``evidence.record_kind =
                       "characterization"`` with ``data_provenance``

TOP-LEVEL KEYS are closed (:data:`MANIFEST_KEYS`): an unknown key such as
``temperaturs_c`` is rejected at load with the manifest path, the unknown
fields and the supported keys. A block that deliberately extends the loader
passes ``load(dir, extra_keys=("my_key",))``; otherwise put block data in
``evidence``.

Booleans, strings, null, NaN and +/-Infinity are rejected; JSON ``NaN`` /
``Infinity`` constants are refused when the manifest is parsed. So is an
object that declares the same key twice -- at the top level, in a
measurement's ``checks`` or inside a ``*_by_axis`` map, even when both
values are identical: the decoder would otherwise silently keep only the
last value (see :mod:`strict_json`).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from . import evidence as evidence_mod
from .corners import (
    DEFAULT_CORNER_SET,
    DEFAULT_NOMINAL_SUPPLY_V,
    DEFAULT_SUPPLY_TOLERANCE,
    DEFAULT_TEMPERATURES_C,
)
from .strict_json import no_duplicate_keys

MANIFEST_NAME = "tb.json"

#: Name of the per-experiment subdirectory that holds the testbench.
TESTBENCH_DIRNAME = "testbench"

#: Canonical names of the directives a fragment may not carry. Matching is
#: by :func:`directive_kind`, i.e. by the spellings ngspice honors, not by
#: these exact strings: ``.inc`` / ``.INCLUDE`` are ``.include``,
#: ``.library`` is ``.lib``, ``.temperature`` is ``.temp``.
FORBIDDEN_DIRECTIVES = (".control", ".endc", ".end", ".lib", ".temp", ".include")

#: How ngspice recognizes the dot-directives this module classifies, as
#: ``(spelling, kind)`` pairs tried in order. ngspice's front end
#: (``src/frontend/inpcom.c``, ``inp.c``) tests each card with
#: ``ciprefix(spelling, line)``: case-insensitive, and satisfied once the
#: whole spelling is consumed, so any suffix also matches (``.params`` is a
#: ``.param``, ``.temperature`` a ``.temp``). Source basis -- ngspice
#: master, inspected 2026-10-11, not a pinned release:
#:
#:   .ends / .eom      ciprefix; inpcom.c rewrites ``.eom`` to ``.ends``
#:   .endc             ciprefix
#:   .end              the exception, see :func:`directive_kind`
#:   .subckt / .macro  ciprefix; inpcom.c rewrites ``.macro`` to ``.subckt``
#:   .param            ciprefix
#:   .control          ciprefix
#:   .inc              inpcom.c tests ``.include`` *or* ``.inc``, and the
#:                     latter, as a prefix, subsumes the former
#:   .lib              ciprefix
#:   .temp             ciprefix (inp.c)
#:
#: Only these spellings are honored; arbitrary abbreviations (``.par``,
#: ``.subc``) are *not* established by the source and are not matched.
#: Order is precedence: the ``.end`` family shares a prefix, so ``.ends``
#: and ``.endc`` are decided before the exact-token ``.end`` rule.
_DIRECTIVE_PREFIXES_BEFORE_END = (
    (".ends", ".ends"),
    (".eom", ".ends"),
    (".endc", ".endc"),
)
_DIRECTIVE_PREFIXES = (
    (".subckt", ".subckt"),
    (".macro", ".subckt"),
    (".param", ".param"),
    (".control", ".control"),
    (".inc", ".include"),
    (".lib", ".lib"),
    (".temp", ".temp"),
)


def directive_kind(word: str) -> str | None:
    """Canonical directive ngspice reads ``word`` (a card's first field) as.

    Returns one of ``.ends``, ``.endc``, ``.end``, ``.subckt``, ``.param``,
    ``.control``, ``.include``, ``.lib``, ``.temp`` -- or ``None`` for
    anything else, including directives this module does not police
    (``.option``, ``.endl``, ...). See :data:`_DIRECTIVE_PREFIXES` for the
    spelling rules and their ngspice source basis.

    ``.end`` is the one exact rule: ngspice treats a card as ``.end`` only
    when nothing alphabetic follows the four characters (inpcom.c requires
    end-of-line or whitespace; inp.c's listing uses ``!isalpha``), which is
    how ``.ends`` / ``.endc`` / ``.endl`` stay distinct. This uses the
    broader ``!isalpha`` form, so ``.end;comment`` is still caught.
    """
    word = word.lower()
    for spelling, kind in _DIRECTIVE_PREFIXES_BEFORE_END:
        if word.startswith(spelling):
            return kind
    if word.startswith(".end") and not word[4:5].isalpha():
        return ".end"
    for spelling, kind in _DIRECTIVE_PREFIXES:
        if word.startswith(spelling):
            return kind
    return None

#: Keys a ``checks`` entry may carry. Anything else is a typo that would
#: otherwise be silently ignored.
CHECK_KEYS = (
    "min",
    "max",
    "max_spread_pct",
    "min_spread_pct",
    "min_spread_pct_by_axis",
    "max_spread_pct_by_axis",
    "description",
)

#: Every top-level key a ``tb.json`` manifest may carry. Anything else is a
#: typo (``temperaturs_c``, ``chekcs``, ``corner``) that would otherwise fall
#: back to a default silently, so :func:`load` rejects it before freezing
#: inputs, reserving evidence or launching a simulator. Block-specific
#: metadata belongs in the ``evidence`` block (``evidence.from_manifest``); a
#: block whose loader intentionally reads more top-level keys passes them as
#: ``load(..., extra_keys=(...))`` (or wraps ``load`` with a fixed set).
MANIFEST_KEYS = (
    "name",
    "netlist",
    "description",
    "claim",
    "netlist_provenance",
    "nominal_supply_v",
    "supply_tolerance",
    "temperatures_c",
    "corners",
    "analyses",
    "measure",
    "params",
    "checks",
    "options",
    "evidence",
)

#: Parameter names the harness itself defines per PVT point. Manifest
#: ``params`` may not reuse them (case-insensitive), and neither may a
#: fragment's top-level ``.param`` assignments
#: (:func:`validate_fragment_param_ownership`); shared by load-time
#: validation and ``runner.compose_deck``.
RESERVED_PVT_PARAMS = ("vdd_val", "vdd_nom", "temp_c")


def reserved_param_collisions(params) -> list[str]:
    """Keys of ``params`` that collide with a reserved PVT name (case-insensitive)."""
    return [k for k in params if str(k).strip().casefold() in RESERVED_PVT_PARAMS]


def _reserved_param_message(key) -> str:
    return (
        f"params.{key} uses the harness-owned PVT parameter name "
        f"{str(key).strip().casefold()!r} (reserved: {', '.join(RESERVED_PVT_PARAMS)}); "
        "set the supply/temperature with 'nominal_supply_v' / 'supply_tolerance' / "
        "'temperatures_c' instead"
    )


#: An assignment target inside a ``.param`` directive's argument text: an
#: identifier followed by ``=``. ``(?!=)`` keeps an equality test inside an
#: expression (``x = a==b ? ...``) from reading as an assignment to ``a``;
#: the lookbehind keeps a longer identifier's tail or a dotted name from
#: matching part-way.
_ASSIGN_NAME_RE = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)")


def _mask_quotes_and_comments(text: str) -> str:
    """``text`` with quoted spans (``'...'`` / ``"..."``) and ``;`` / ``$``
    comments replaced by spaces, same length, so regex match offsets -- and
    the physical line numbers derived from them -- stay meaningful. A
    quoted expression may *use* a reserved name freely; only an assignment
    to one collides, and a comment mentioning one collides with nothing.
    Called on one physical line at a time (see :func:`_logical_lines`), so
    a comment masks only to its own line's end."""
    out = list(text)
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "'\"":
            end = text.find(ch, i + 1)
            end = len(text) if end < 0 else end + 1
            for k in range(i, end):
                out[k] = " "
            i = end
        elif ch in ";$":
            for k in range(i, len(text)):
                out[k] = " "
            break
        else:
            i += 1
    return "".join(out)


def _logical_lines(text: str) -> list[tuple[list[tuple[int, int, int]], str]]:
    """SPICE logical lines as ``(segments, joined)`` pairs.

    A physical line whose first non-blank character is ``+`` continues the
    previous logical line (the ``+`` counts as the separating space); blank
    and ``*`` comment lines are skipped and do not interrupt a continuation.
    Quoted spans and ``;`` / ``$`` comments are masked -- same-length
    spaces -- on each physical line *before* continuations are joined, the
    order ngspice itself preprocesses a deck (strip comments, then stitch
    continuations): a comment therefore ends at its own line's end and
    cannot hide an assignment on a later continuation line. ``segments``
    maps offsets in ``joined`` back to physical line numbers via
    ``(start, length, lineno)`` spans, one per contributing physical
    line, so a match found in the joined text can report the line its
    assigned name actually sits on.
    """
    logical: list[tuple[list[tuple[int, int, int]], str]] = []
    segments: list[tuple[int, int, int]] = []
    parts: list[str] = []
    offset = 0

    def close() -> None:
        nonlocal segments, parts, offset
        if parts:
            logical.append((segments, "".join(parts)))
        segments, parts, offset = [], [], 0

    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("*"):
            continue
        if stripped.startswith("+") and parts:
            body = _mask_quotes_and_comments(stripped[1:].strip())
            if body.strip():
                segments.append((offset + 1, len(body), lineno))
                parts.append(" " + body)
                offset += 1 + len(body)
            continue
        close()
        masked = _mask_quotes_and_comments(stripped)
        segments.append((0, len(masked), lineno))
        parts.append(masked)
        offset = len(masked)
    close()
    return logical


def _segment_line(segments: list[tuple[int, int, int]], pos: int) -> int:
    """Physical line number for offset ``pos`` within a joined logical line."""
    for start, length, lineno in segments:
        if start <= pos < start + length:
            return lineno
    return segments[-1][2] if segments else 0


def reserved_fragment_param_assignments(text: str) -> list[tuple[int, str]]:
    """``(line, name)`` for every *top-level* ``.param`` assignment in
    ``text`` to a reserved PVT name (:data:`RESERVED_PVT_PARAMS`,
    case-insensitive).

    Top-level means outside any ``.subckt`` ... ``.ends`` body -- the scope
    rule. A global ``.param vdd_val=1.0`` competes with the harness's own
    per-PVT-point definition, while a subcircuit-local ``.param`` (or a
    parameter on the ``.subckt`` line itself, which is not a ``.param``
    directive) is scoped to that subcircuit and stays supported.
    Continuation lines and multiple assignments per directive are handled;
    the reported line is the physical line the assigned name sits on.
    Quoted spans and comments are already masked per physical line by
    :func:`_logical_lines`.

    Directives are recognized the way ngspice recognizes them
    (:func:`directive_kind`): ``.params`` / ``.PARAMETERS`` assign like
    ``.param``, and the ``.macro`` / ``.eom`` aliases open and close a
    subcircuit scope like ``.subckt`` / ``.ends``.
    """
    problems: list[tuple[int, str]] = []
    depth = 0
    for segments, joined in _logical_lines(text):
        fields = joined.split(None, 1)
        directive = directive_kind(fields[0])
        if directive == ".subckt":
            depth += 1
        elif directive == ".ends":
            depth = max(depth - 1, 0)
        elif directive == ".param" and depth == 0:
            rest = fields[1] if len(fields) > 1 else ""
            # ``rest`` is a suffix of ``joined`` (split strips only the
            # whitespace run after the directive), so this is its offset.
            base = len(joined) - len(rest)
            for match in _ASSIGN_NAME_RE.finditer(rest):
                name = match.group(1)
                if name.casefold() in RESERVED_PVT_PARAMS:
                    problems.append((_segment_line(segments, base + match.start()), name))
    return problems


def validate_fragment_param_ownership(tb: Testbench) -> None:
    """Reject a fragment whose top-level ``.param`` assigns a reserved PVT
    name, naming the fragment path and each offending line.

    Called from :func:`validate_netlist` (hence by :func:`load`, before
    inputs are frozen, evidence reserved or a worker launched) and again by
    ``runner.compose_deck``, so neither a loaded nor a hand-built
    Testbench can compose competing definitions of the quantities the
    recorded PVT tuple claims to control. The captured netlist bytes are
    checked, not a later read of the live file.
    """
    problems = reserved_fragment_param_assignments(
        tb.captured_netlist().decode("utf-8"))
    if problems:
        detail = "\n".join(
            f"  line {line}: assigns {name!r}" for line, name in problems)
        raise ValueError(
            f"{tb.netlist}: netlist fragment must not assign the harness-owned "
            f"PVT parameters ({', '.join(RESERVED_PVT_PARAMS)}) at the top level -- "
            "the harness defines them per PVT point ahead of the fragment include, "
            "so a fragment definition competes with the recorded PVT tuple. Set the "
            "supply/temperature with 'nominal_supply_v' / 'supply_tolerance' / "
            "'temperatures_c', or scope the name to a subcircuit (a .param inside "
            ".subckt ... .ends, or on the .subckt line):\n" + detail
        )


def _manifest_params(manifest: dict, path: Path) -> dict:
    params = manifest.get("params", {})
    if not isinstance(params, dict):
        raise ValueError(
            f"{path}: 'params' must be a JSON object, got {type(params).__name__}"
        )
    for key in reserved_param_collisions(params):
        raise ValueError(f"{path}: {_reserved_param_message(key)}")
    return dict(params)


#: Axis names accepted by the per-axis sensitivity checks.
AXES = ("process", "temperature", "supply")

PROVENANCE_RULE = (
    "netlist_provenance must be 'schematic', 'schematic (<detail>)' or start with "
    "'extracted' -- 'schematic' for a design/ netlist, 'extracted' for a post-layout "
    "one, 'schematic (<detail>)' for a parametric variant of the schematic deck run "
    "at a deliberately different device value (neither the ratified schematic deck "
    "nor ever taken through layout, so calling it either would misstate it)"
)


def valid_netlist_provenance(value: str) -> bool:
    """Is ``value`` an acceptable ``netlist_provenance`` field? See
    :data:`PROVENANCE_RULE` for the three accepted forms and why the middle
    one exists."""
    return (
        value == "schematic"
        or value.startswith("schematic (")
        or value.startswith("extracted")
    )


@dataclass
class Testbench:
    directory: Path
    name: str
    netlist: Path
    description: str = ""
    claim: str = ""
    netlist_provenance: str = "schematic"
    nominal_supply_v: float = DEFAULT_NOMINAL_SUPPLY_V
    supply_tolerance: float = DEFAULT_SUPPLY_TOLERANCE
    temperatures_c: tuple[float, ...] = DEFAULT_TEMPERATURES_C
    corners: tuple[str, ...] = (DEFAULT_CORNER_SET,)
    analyses: tuple[str, ...] = ("op",)
    measure: dict[str, str] = field(default_factory=dict)
    params: dict[str, str | float] = field(default_factory=dict)
    checks: dict[str, dict] = field(default_factory=dict)
    options: tuple[str, ...] = ()
    evidence: evidence_mod.BaseExtensions = field(default_factory=evidence_mod.BaseExtensions)
    #: The exact bytes of ``tb.json`` / the netlist fragment that :func:`load`
    #: parsed and validated (product#229). Every hash, snapshot and frozen
    #: copy derives from these, never from a later read of the live files.
    #: ``None`` only for a hand-built Testbench, which falls back to reading
    #: the live file.
    manifest_bytes: bytes | None = field(default=None, repr=False, compare=False)
    netlist_bytes: bytes | None = field(default=None, repr=False, compare=False)
    #: Run-owned immutable copies written by :func:`freeze`; ``None`` until
    #: then. When set, composed decks include ``frozen_netlist``.
    frozen_netlist: Path | None = field(default=None, repr=False, compare=False)
    frozen_manifest: Path | None = field(default=None, repr=False, compare=False)

    @property
    def experiment(self) -> str:
        """``sim/<experiment-slug>/testbench/tb.json`` -> ``<experiment-slug>``."""
        return self.directory.parent.name

    @property
    def experiment_dir(self) -> Path:
        """``sim/<experiment-slug>/`` -- where records/corners/snapshots live."""
        return self.directory.parent

    def captured_netlist(self) -> bytes:
        """The netlist bytes this testbench was validated against."""
        if self.netlist_bytes is None:
            return self.netlist.read_bytes()
        return self.netlist_bytes

    def captured_manifest(self) -> bytes:
        """The manifest bytes this testbench was parsed from."""
        if self.manifest_bytes is None:
            return (self.directory / MANIFEST_NAME).read_bytes()
        return self.manifest_bytes

    @property
    def include_path(self) -> Path:
        """What composed decks ``.include``: the frozen copy once
        :func:`freeze` has run, else the live fragment."""
        return self.frozen_netlist if self.frozen_netlist is not None else self.netlist

    @property
    def netlist_sha256(self) -> str:
        return hashlib.sha256(self.captured_netlist()).hexdigest()

    @property
    def manifest_sha256(self) -> str:
        return hashlib.sha256(self.captured_manifest()).hexdigest()

    def _frozen_ref(self, path: Path | None) -> str:
        """``path`` relative to the experiment directory (POSIX form), or
        ``""`` if there is no frozen copy or it lies outside that directory
        (e.g. disposable scratch) -- never an absolute host path."""
        if path is None:
            return ""
        try:
            return (
                Path(os.path.abspath(path))
                .relative_to(Path(os.path.abspath(self.experiment_dir)))
                .as_posix()
            )
        except ValueError:
            return ""

    def provenance(self) -> dict:
        frozen = {}
        manifest_ref = self._frozen_ref(self.frozen_manifest)
        netlist_ref = self._frozen_ref(self.frozen_netlist)
        if manifest_ref and netlist_ref:
            frozen = {"frozen_manifest": manifest_ref, "frozen_netlist": netlist_ref}
        return {
            **frozen,
            "name": self.name,
            "description": self.description,
            "claim": self.claim,
            "experiment": self.experiment,
            "directory": self.directory.name,
            "netlist": self.netlist.name,
            "netlist_sha256": self.netlist_sha256,
            "netlist_provenance": self.netlist_provenance,
            "manifest_sha256": self.manifest_sha256,
            "nominal_supply_v": self.nominal_supply_v,
            "supply_tolerance": self.supply_tolerance,
        }


def _require(manifest: dict, key: str, path: Path):
    if key not in manifest:
        raise ValueError(f"{path}: missing required key {key!r}")
    return manifest[key]


def _finite_number(value, where: str, path: Path) -> float:
    """``value`` as a float, or ValueError naming ``where`` if not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{path}: {where} must be a finite number, got {type(value).__name__} {value!r}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{path}: {where} must be a finite number, got {value!r}")
    return number


def _reject_constant(name: str):
    raise ValueError(f"non-finite JSON constant {name} is not allowed")


def _pvt_axes(manifest: dict, path: Path) -> tuple[float, float, tuple[float, ...]]:
    nominal = _finite_number(
        manifest.get("nominal_supply_v", DEFAULT_NOMINAL_SUPPLY_V), "'nominal_supply_v'", path
    )
    if nominal <= 0:
        raise ValueError(f"{path}: 'nominal_supply_v' must be > 0, got {nominal!r}")
    tol = _finite_number(
        manifest.get("supply_tolerance", DEFAULT_SUPPLY_TOLERANCE), "'supply_tolerance'", path
    )
    if not 0 <= tol < 1:
        raise ValueError(
            f"{path}: 'supply_tolerance' must satisfy 0 <= tolerance < 1 "
            f"(a fraction, e.g. 0.1 for +/-10%), got {tol!r}"
        )
    temps = manifest.get("temperatures_c", DEFAULT_TEMPERATURES_C)
    if not isinstance(temps, (list, tuple)):
        raise ValueError(
            f"{path}: 'temperatures_c' must be a JSON array of numbers, got {type(temps).__name__}"
        )
    if not temps:
        raise ValueError(f"{path}: 'temperatures_c' is empty; give at least one temperature")
    return nominal, tol, tuple(
        _finite_number(t, f"'temperatures_c'[{i}]", path) for i, t in enumerate(temps)
    )


def _validate_checks(checks: dict[str, dict], measure: dict[str, str], path: Path) -> None:
    for name, spec in checks.items():
        if name not in measure:
            raise ValueError(
                f"{path}: check {name!r} does not name a measurement "
                f"(known: {', '.join(sorted(measure))})"
            )
        if not isinstance(spec, dict):
            raise ValueError(f"{path}: check {name!r} must be an object, got {type(spec).__name__}")
        unknown = sorted(set(spec) - set(CHECK_KEYS))
        if unknown:
            raise ValueError(
                f"{path}: check {name!r} has unknown key(s) {', '.join(unknown)}; "
                f"known: {', '.join(CHECK_KEYS)}"
            )
        for axis_key in ("min_spread_pct_by_axis", "max_spread_pct_by_axis"):
            axes = spec.get(axis_key)
            if axes is None:
                continue
            if not isinstance(axes, dict):
                raise ValueError(f"{path}: check {name!r}: {axis_key} must be an object")
            unknown_axes = sorted(set(axes) - set(AXES))
            if unknown_axes:
                raise ValueError(
                    f"{path}: check {name!r}: unknown axis/axes {', '.join(unknown_axes)} in "
                    f"{axis_key}; known: {', '.join(AXES)}"
                )
            for axis, limit in axes.items():
                _finite_number(limit, f"check {name!r}: {axis_key}[{axis!r}]", path)
        for key, limit in spec.items():
            if key == "description":
                if not isinstance(limit, str):
                    raise ValueError(
                        f"{path}: check {name!r}: 'description' must be a string, "
                        f"got {type(limit).__name__}"
                    )
            elif key not in ("min_spread_pct_by_axis", "max_spread_pct_by_axis"):
                _finite_number(limit, f"check {name!r}: {key!r}", path)


#: Check keys that are executable limits (``description`` is prose).
LIMIT_KEYS = tuple(k for k in CHECK_KEYS if k != "description")


def has_executable_limit(checks: dict) -> bool:
    """Does any check carry a real limit? ``description`` and empty
    ``*_by_axis`` objects do not count."""
    for spec in checks.values():
        for key in LIMIT_KEYS:
            if key in spec and (spec[key] or key not in (
                    "min_spread_pct_by_axis", "max_spread_pct_by_axis")):
                return True
    return False


def _require_executable_limits(checks: dict, path: Path) -> None:
    if has_executable_limit(checks):
        return
    raise ValueError(
        f"{path}: a corner-matrix manifest must define at least one executable limit in "
        f"'checks' (one of {', '.join(LIMIT_KEYS)}); omitted, empty and description-only "
        "checks test no specification, so a passing run would not verify anything. "
        "Either add a real limit, or, for a measurement-only experiment, declare "
        "\"evidence\": {\"record_kind\": \"characterization\", \"data_provenance\": "
        "\"<how the values were obtained>\"}. Do not invent limits just to satisfy this check."
    )


def _manifest_corners(manifest: dict, path: Path) -> tuple[str, ...]:
    """The manifest's corner selection, shape-checked.

    Absent means the generic :data:`corners.DEFAULT_CORNER_SET`. Present
    means a non-empty JSON array of non-blank corner / corner-set names --
    an empty array or a bare string is refused here rather than silently
    running some other matrix. Whether each name is *registered* is checked
    when the CLI resolves the selection (corners.resolve_corners), so a
    block's own ``register_corner_set()`` calls are honoured.
    """
    if "corners" not in manifest:
        return (DEFAULT_CORNER_SET,)
    corners = manifest["corners"]
    if not isinstance(corners, list):
        raise ValueError(
            f"{path}: 'corners' must be a JSON array of corner / corner-set names, "
            f"got {type(corners).__name__}"
        )
    if not corners:
        raise ValueError(f"{path}: 'corners' is empty; omit it to use the default set "
                         f"{DEFAULT_CORNER_SET!r}")
    bad = [c for c in corners if not isinstance(c, str) or not c.strip()]
    if bad:
        raise ValueError(f"{path}: 'corners' entries must be non-empty strings, got {bad!r}")
    return tuple(c.strip() for c in corners)


def _manifest_string_array(manifest: dict, path: Path, field: str,
                           default: tuple[str, ...], allow_empty: bool) -> tuple[str, ...]:
    """A simulator-command array field (``analyses`` / ``options``), shape-checked.

    Absent means ``default``. Present means a JSON array of non-blank strings
    (non-empty unless ``allow_empty``). A bare string would otherwise be split
    into characters by ``tuple()``. Entries are full ngspice command / option
    text and are passed through verbatim: there is no command allowlist.
    """
    if field not in manifest:
        return default
    value = manifest[field]
    if not isinstance(value, list):
        raise ValueError(
            f"{path}: '{field}' must be a JSON array of strings, got {type(value).__name__}"
        )
    if not value and not allow_empty:
        raise ValueError(f"{path}: '{field}' is empty; omit it to use the default "
                         f"{list(default)!r}")
    for index, entry in enumerate(value):
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(
                f"{path}: '{field}'[{index}] must be a non-blank string, got {entry!r}"
            )
    return tuple(value)


def _check_manifest_keys(manifest: dict, path: Path, extra_keys: tuple[str, ...]) -> None:
    allowed = MANIFEST_KEYS + tuple(k for k in extra_keys if k not in MANIFEST_KEYS)
    unknown = sorted(set(manifest) - set(allowed), key=str)
    if unknown:
        raise ValueError(
            f"{path}: unknown top-level manifest field(s) {', '.join(map(repr, unknown))} "
            f"(a misspelt field would silently fall back to its default); "
            f"supported: {', '.join(allowed)}. Block-specific data belongs in the "
            "'evidence' block"
        )


def load(directory: str | Path, extra_keys: tuple[str, ...] = ()) -> Testbench:
    """Load a testbench manifest into a :class:`Testbench`.

    Unknown top-level manifest keys are rejected; ``extra_keys`` lets a block
    that intentionally extends the loader add to the allowed set.

    Accepts the experiment directory (``sim/<slug>/``), its ``testbench/``
    subdirectory, or the ``tb.json`` path itself.
    """
    directory = Path(directory).resolve()
    if directory.is_file() and directory.name == MANIFEST_NAME:
        directory = directory.parent
    if (directory / TESTBENCH_DIRNAME / MANIFEST_NAME).is_file():
        directory = directory / TESTBENCH_DIRNAME
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no {MANIFEST_NAME} in {directory}")

    # Capture once (product#229): parsing, validation, hashes, the frozen copy
    # and the snapshot all use these bytes, so a concurrent edit of the live
    # files cannot make them disagree.
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(
            manifest_bytes.decode("utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=no_duplicate_keys,
        )
    except ValueError as exc:
        raise ValueError(f"{manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"{manifest_path}: manifest must be a JSON object")
    _check_manifest_keys(manifest, manifest_path, tuple(extra_keys))
    nominal_supply_v, supply_tolerance, temperatures_c = _pvt_axes(manifest, manifest_path)
    params = _manifest_params(manifest, manifest_path)
    analyses = _manifest_string_array(manifest, manifest_path, "analyses", ("op",), False)
    options = _manifest_string_array(manifest, manifest_path, "options", (), True)

    netlist = directory / _require(manifest, "netlist", manifest_path)
    if not netlist.is_file():
        raise FileNotFoundError(f"{manifest_path}: netlist {netlist} does not exist")
    netlist_bytes = netlist.read_bytes()

    raw_measure = _require(manifest, "measure", manifest_path)
    if not isinstance(raw_measure, dict):
        raise ValueError(
            f"{manifest_path}: 'measure' must be a JSON object mapping measurement "
            f"names to ngspice expressions, got {type(raw_measure).__name__}"
        )
    measure = dict(raw_measure)
    if not measure:
        raise ValueError(f"{manifest_path}: 'measure' must define at least one measurement")
    for key, expr in measure.items():
        if not key.replace("_", "").isalnum():
            raise ValueError(
                f"{manifest_path}: measurement name {key!r} must be alphanumeric/underscore "
                "(it becomes an ngspice vector name)"
            )
        if key != key.lower():
            # ngspice folds vector names to lower case, so an upper-case
            # measurement name is printed back lower-cased and the runner's
            # exact-name match never finds it -- refuse at load time instead
            # of discovering it after the whole grid has already run.
            raise ValueError(
                f"{manifest_path}: measurement name {key!r} must be lower case "
                "(ngspice folds vector names)"
            )
        if not isinstance(expr, str) or not expr.strip():
            raise ValueError(
                f"{manifest_path}: measurement {key!r} must map to a non-blank "
                f"expression string, got {type(expr).__name__} {expr!r}"
            )

    raw_checks = manifest.get("checks", {})
    if not isinstance(raw_checks, dict):
        raise ValueError(
            f"{manifest_path}: 'checks' must be a JSON object mapping measurement "
            f"names to limit objects, got {type(raw_checks).__name__}"
        )
    checks = dict(raw_checks)
    _validate_checks(checks, measure, manifest_path)

    extensions = evidence_mod.from_manifest(manifest.get("evidence", {}))
    extensions.validate()
    if not extensions.is_characterization:
        _require_executable_limits(checks, manifest_path)

    netlist_provenance = str(manifest.get("netlist_provenance", "schematic"))
    if not valid_netlist_provenance(netlist_provenance):
        raise ValueError(f"{manifest_path}: {PROVENANCE_RULE}; got {netlist_provenance!r}")

    tb = Testbench(
        directory=directory,
        name=manifest.get("name", directory.parent.name),
        netlist=netlist,
        description=manifest.get("description", ""),
        claim=manifest.get("claim", ""),
        netlist_provenance=netlist_provenance,
        nominal_supply_v=nominal_supply_v,
        supply_tolerance=supply_tolerance,
        temperatures_c=temperatures_c,
        corners=_manifest_corners(manifest, manifest_path),
        analyses=analyses,
        measure=measure,
        params=params,
        checks=checks,
        options=options,
        evidence=extensions,
        manifest_bytes=manifest_bytes,
        netlist_bytes=netlist_bytes,
    )
    validate_netlist(tb)
    return tb


def _write_frozen(path: Path, data: bytes) -> None:
    """Exclusively create ``path`` with ``data``, verify it, make it read-only."""
    with open(path, "xb") as fh:
        fh.write(data)
    if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(data).digest():
        raise OSError(f"{path}: frozen copy does not match the captured bytes")
    try:  # best effort; not every platform/filesystem honours it
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    except OSError:
        pass


def freeze(tb: Testbench, directory: str | Path) -> Path:
    """Write the captured manifest and netlist fragment into ``directory``
    (created if missing; files are created exclusively, never overwritten) and
    point ``tb`` at them: every deck composed afterwards includes the frozen
    fragment, so editing the original files mid-run changes nothing.

    Call before launching any point. A recorded run freezes into its reserved
    evidence bundle (``corners/<record-id>/frozen/``); a disposable run into
    its scratch workspace. Returns the directory (absolute). Raises OSError
    on failure.
    """
    out = Path(os.path.abspath(directory))
    out.mkdir(parents=True, exist_ok=True)
    netlist_dst = out / tb.netlist.name
    manifest_dst = out / MANIFEST_NAME
    if netlist_dst == manifest_dst:  # a fragment literally named tb.json
        raise OSError(f"{tb.netlist}: fragment name collides with {MANIFEST_NAME}")
    _write_frozen(manifest_dst, tb.captured_manifest())
    _write_frozen(netlist_dst, tb.captured_netlist())
    tb.frozen_manifest = manifest_dst
    tb.frozen_netlist = netlist_dst
    return out


def validate_netlist(tb: Testbench) -> None:
    """Reject fragments that try to own what the harness owns.

    Catching this here is much friendlier than debugging a duplicated
    ``.end`` or a hardcoded ``.temp 27`` that silently pins every corner to
    room temperature. The same goes for a top-level ``.param`` assignment
    to a reserved PVT name (see :func:`validate_fragment_param_ownership`
    for the scope rule): load runs this before anything is frozen,
    reserved or launched.

    Directives are matched by the spellings ngspice honors
    (:func:`directive_kind`), in any letter case: ``.inc`` is ``.include``,
    ``.library`` is ``.lib``, ``.temperature`` is ``.temp``. The legal
    subcircuit terminators ``.ends`` / ``.eom`` are not ``.end``.
    """
    problems: list[str] = []
    text = tb.captured_netlist().decode("utf-8")  # the captured bytes, not the live file
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line.startswith("."):
            continue
        if directive_kind(line.split()[0]) in FORBIDDEN_DIRECTIVES:
            problems.append(f"  line {lineno}: {line}")
    if problems:
        raise ValueError(
            f"{tb.netlist}: netlist fragments must not contain "
            f"{', '.join(FORBIDDEN_DIRECTIVES)} (in any spelling ngspice accepts, "
            "e.g. .inc, .library, .temperature) -- the harness supplies the models, "
            "corner libs, temperature and control block:\n" + "\n".join(problems)
        )
    validate_fragment_param_ownership(tb)


def discover(root: str | Path) -> list[Path]:
    """Every experiment directory under ``root`` that owns a testbench.

    Looks for ``<root>/<experiment-slug>/testbench/tb.json`` and returns the
    ``<experiment-slug>`` directories, sorted.
    """
    root = Path(root)
    return sorted(
        p.parent.parent for p in root.glob(f"*/{TESTBENCH_DIRNAME}/{MANIFEST_NAME}")
    )
