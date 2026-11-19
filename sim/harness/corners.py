"""Process / voltage / temperature corner definitions and PVT grid mechanics.

GENERIC CORE:

- The classic five-corner MOS process sweep (``tt``, ``ff``, ``ss``, ``fs``,
  ``sf``) most open PDKs ship a ``.lib`` section for, registered below as the
  default ``CORNER_SETS["mos"]``. ``CORNER_SETS["tt"]`` is the reduced,
  single-corner smoke-test set (not a valid evidence matrix on its own).
- :class:`Corner`, :class:`PvtPoint`, :func:`build_grid`, :func:`supply_points`,
  :func:`resolve_corners` -- the PVT-grid arithmetic itself (a corner ×
  temperature × supply full factorial, in a stable order).
- :func:`select_corner_names` -- which selection a run uses: an explicit
  ``--corners`` override, else the testbench manifest's ``corners``, else
  :data:`DEFAULT_CORNER_SET`. Empty or unknown selections are a
  :class:`CornerSelectionError`, never a silent fallback.
- :func:`sabotage` -- the harness's negative control: forces every corner's
  sections to typical so a corner-runner self-test can prove that switching
  corners actually changes what gets simulated, rather than silently
  simulating typical everywhere no matter what was asked for.

EXTENSION POINT -- corner sections don't generalize across PDKs (a PDK's
``.lib`` section names for "fast", "slow", or a device family other than MOS
are PDK-specific), and *which* additional corners matter is block-specific
(a SAR ADC cares about CDAC capacitor corners; a bandgap cares about
resistor and BJT corners; plenty of blocks need only the five MOS corners
as-is). Two ways to extend:

1. Edit the ``sections`` tuple on the built-in :data:`CORNERS` entries if
   this PDK's model library uses different section names than the
   ``tt``/``ff``/``ss``/``fs``/``sf`` default (most open PDKs use exactly
   these names; if not, this is the one edit new to this PDK).
2. Call :func:`register_corner` (and optionally :func:`register_corner_set`)
   to add block-specific corners on top -- see
   ``gf180-sar-adc/sim/harness/corners.py`` for a worked example that adds
   MiM/MOS-cap-dominated corners for a CDAC-heavy block.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

# Default PVT axes. A block's own CLAUDE.md / spec should state its PVT
# requirement explicitly; these are reasonable open-PDK defaults to start
# from, not a substitute for that statement.
DEFAULT_TEMPERATURES_C: tuple[float, ...] = (-40.0, 27.0, 125.0)
DEFAULT_SUPPLY_TOLERANCE: float = 0.10  # +/-10 %
DEFAULT_NOMINAL_SUPPLY_V: float = 1.8   # FILL: this block's nominal supply

#: Device families in the order their ``.lib`` sections are included in the
#: generated deck (see runner.compose_deck). The generic core sweeps only
#: "mos" by default; a block adds families (and per-corner sections for them)
#: as it needs them.
FAMILIES: tuple[str, ...] = ("mos",)


@dataclass(frozen=True)
class Corner:
    """A named process corner: an ordered list of model ``.lib`` sections,
    one per entry in :data:`FAMILIES`."""

    name: str
    sections: tuple[str, ...]
    description: str = ""


#: The classic five MOS corners. Section names match FAMILIES = ("mos",);
#: most open PDKs use exactly these strings as their `.lib` section names --
#: if this PDK's model library uses different ones, edit the tuples below
#: rather than the surrounding mechanics.
CORNERS: dict[str, Corner] = {
    "tt": Corner("tt", ("tt",), "typical NMOS / typical PMOS"),
    "ff": Corner("ff", ("ff",), "fast NMOS / fast PMOS"),
    "ss": Corner("ss", ("ss",), "slow NMOS / slow PMOS"),
    "fs": Corner("fs", ("fs",), "fast NMOS / slow PMOS"),
    "sf": Corner("sf", ("sf",), "slow NMOS / fast PMOS"),
}

CORNER_SETS: dict[str, tuple[str, ...]] = {
    # Minimum bar for a quick smoke run. Not a valid evidence matrix on its own.
    "tt": ("tt",),
    # The five classic MOS corners -- the default.
    "mos": ("tt", "ff", "ss", "fs", "sf"),
}
DEFAULT_CORNER_SET = "mos"


def register_corner(
    name: str, sections: tuple[str, ...], description: str = "", *, sets: tuple[str, ...] = ()
) -> None:
    """Extension point: add a block-specific corner (and optionally the
    corner set(s) it belongs to) on top of the generic core.

    ``sections`` must have one entry per :data:`FAMILIES` in order -- add to
    ``FAMILIES`` first if this corner needs a device family the core does not
    already sweep (e.g. a capacitor or resistor family).
    """
    if len(sections) != len(FAMILIES):
        raise ValueError(
            f"corner {name!r}: {len(sections)} section(s) given, "
            f"expected one per family {FAMILIES!r}"
        )
    CORNERS[name] = Corner(name=name, sections=sections, description=description)
    for set_name in sets:
        CORNER_SETS[set_name] = CORNER_SETS.get(set_name, ()) + (name,)


def register_corner_set(name: str, corner_names: tuple[str, ...]) -> None:
    """Extension point: name a group of (already-registered) corners."""
    unknown = [c for c in corner_names if c not in CORNERS]
    if unknown:
        raise KeyError(f"corner set {name!r} references unknown corner(s): {unknown}")
    CORNER_SETS[name] = tuple(corner_names)


class CornerSelectionError(KeyError, ValueError):
    """A corner selection that cannot be resolved: empty, blank, or naming a
    corner / corner set that is not registered.

    Subclasses ``KeyError`` (what :func:`resolve_corners` raised for unknown
    names before) and ``ValueError`` (what manifest validation raises), so
    existing ``except`` clauses keep working. ``str()`` is the plain message,
    not ``KeyError``'s quoted repr, so a CLI can print it as-is.
    """

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


def parse_corner_list(text: str) -> list[str]:
    """Split a comma-separated ``--corners`` value into names.

    Surrounding whitespace is ignored; an empty value or an empty element
    (``""``, ``"mos,"``, ``" , "``) is a :class:`CornerSelectionError` rather
    than silently meaning "the default".
    """
    names = [part.strip() for part in text.split(",")]
    if not any(names):
        raise CornerSelectionError("empty corner selection; name at least one corner or corner set")
    if not all(names):
        raise CornerSelectionError(f"corner selection {text!r} contains an empty name")
    return names


def select_corner_names(
    cli_names: list[str] | tuple[str, ...] | None,
    manifest_names: list[str] | tuple[str, ...] | None,
) -> list[str]:
    """Which corner / corner-set names a run should use.

    Precedence: an explicit CLI selection (``cli_names`` not ``None``) wins;
    otherwise the testbench manifest's ``corners`` selection; otherwise the
    generic :data:`DEFAULT_CORNER_SET`. A selection that is present but
    empty is an error, never a fallback to the next source.
    """
    for source, names in (("--corners", cli_names), ("manifest 'corners'", manifest_names)):
        if names is None:
            continue
        names = list(names)
        if not names:
            raise CornerSelectionError(f"{source}: empty corner selection")
        return names
    return [DEFAULT_CORNER_SET]


def resolve_corners(names: list[str] | tuple[str, ...] | None) -> list[Corner]:
    """Turn a list of corner *or* corner-set names into Corner objects.

    ``None`` means the generic :data:`DEFAULT_CORNER_SET`. An empty list, a
    blank name, or a name that is neither a registered corner nor a
    registered corner set raises :class:`CornerSelectionError`.
    """
    if names is None:
        names = [DEFAULT_CORNER_SET]
    names = list(names)
    if not names:
        raise CornerSelectionError("empty corner selection; name at least one corner or corner set")
    resolved: list[Corner] = []
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str) or not name.strip():
            raise CornerSelectionError(f"invalid corner name {name!r}")
        expanded = CORNER_SETS.get(name, (name,))
        for corner_name in expanded:
            if corner_name in seen:
                continue
            if corner_name not in CORNERS:
                raise CornerSelectionError(
                    f"unknown corner {corner_name!r}; "
                    f"known corners: {', '.join(sorted(CORNERS))}; "
                    f"known sets: {', '.join(sorted(CORNER_SETS))}"
                )
            seen.add(corner_name)
            resolved.append(CORNERS[corner_name])
    return resolved


def sabotage(corner_list: list[Corner]) -> list[Corner]:
    """Return the same corner *names* with every section forced to typical.

    This is the harness's negative control, not a feature: it reproduces the
    exact silent failure mode a corner sweep is most exposed to -- a runner
    that appears to sweep process corners but actually simulates typical
    everywhere (wrong model include, ignored parameter, wrong section name).
    A self-test should run once normally and once sabotaged; the sabotaged
    run's per-axis sensitivity checks (see report.axis_sensitivity) MUST
    fail. If they pass, corner switching is not taking effect and every
    downstream evidence record is worthless. Force ``--no-write`` whenever
    this is used -- a sabotaged run must never enter the evidence tree.
    """
    typical = CORNERS["tt"].sections
    return [
        Corner(name=corner.name, sections=typical, description=f"SABOTAGED ({corner.description})")
        for corner in corner_list
    ]


def supply_points(
    nominal_v: float = DEFAULT_NOMINAL_SUPPLY_V,
    tolerance: float = DEFAULT_SUPPLY_TOLERANCE,
) -> list[float]:
    """Nominal supply and its +/- tolerance rails, low to high."""
    if tolerance <= 0:
        return [round(nominal_v, 6)]
    return [
        round(nominal_v * (1.0 - tolerance), 6),
        round(nominal_v, 6),
        round(nominal_v * (1.0 + tolerance), 6),
    ]


def _format_supply(vdd: float) -> str:
    """Shortest fixed-point text of ``vdd`` with >= 2 and <= 6 decimals."""
    text = f"{vdd:.6f}".rstrip("0")
    whole, _, frac = text.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


class DuplicateArtifactError(ValueError):
    """Two grid points map to the same per-point artifact id."""


def check_unique_artifact_ids(points: "list[PvtPoint]") -> None:
    """Raise :class:`DuplicateArtifactError` if any two points share a
    ``corner_id`` (and so would overwrite each other's deck and log)."""
    seen: dict[str, PvtPoint] = {}
    dupes: list[str] = []
    for point in points:
        cid = point.corner_id
        if cid in seen:
            dupes.append(cid)
        seen[cid] = point
    if dupes:
        raise DuplicateArtifactError(
            "duplicate PVT artifact id(s) in grid: "
            + ", ".join(sorted(set(dupes)))
            + "; remove duplicate corners/temperatures/supplies"
        )


@dataclass(frozen=True)
class PvtPoint:
    """One point in the PVT grid -- exactly one ngspice invocation."""

    corner: Corner
    temp_c: float
    vdd: float
    index: int = field(default=0, compare=False)

    @property
    def corner_id(self) -> str:
        """``<process>_<temp>c_<supply>v`` -- the per-corner log/evidence id.

        The supply keeps at least two decimals (``1.80``) and grows to as many
        as needed (up to 6, matching :func:`supply_points` rounding) so close
        supplies such as 1.801 and 1.804 get distinct ids. Grids whose
        supplies are exact to two decimals (the defaults) keep their
        historical ids.
        """
        return f"{self.corner.name}_{self.temp_c:g}c_{_format_supply(self.vdd)}v"

    def as_dict(self) -> dict:
        return {
            "corner": self.corner.name,
            "corner_sections": list(self.corner.sections),
            "temp_c": self.temp_c,
            "vdd": self.vdd,
            "corner_id": self.corner_id,
        }


def build_grid(
    corners: list[Corner],
    temperatures: list[float] | tuple[float, ...],
    supplies: list[float],
) -> list[PvtPoint]:
    """Full factorial P x V x T grid, in a stable, reproducible order."""
    return [
        PvtPoint(corner=corner, temp_c=float(temp), vdd=float(vdd), index=i)
        for i, (corner, temp, vdd) in enumerate(
            itertools.product(corners, temperatures, supplies)
        )
    ]
