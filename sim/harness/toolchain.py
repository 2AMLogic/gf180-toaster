"""Pinned toolchain versions, and the loud check that they still hold.

GENERIC CORE, unmodified in spirit from the reference implementation this
package was distilled from -- toolchain pin/drift checking has no
block-specific content by name. A PDK's device model set (identified by its
own install-revision marker, e.g. an ``open_pdks`` hash for volare-installed
PDKs) **is** the model data every measured value is taken against; a run
against a different revision produces numbers that are silently
incomparable with every other record under ``sim/``. So the pins recorded in
``sim/toolchain.json`` are checked *before* any point is simulated:

- **pdk_version** -- exact match against whatever :meth:`pdk.Pdk.version`
  reports (an ``open_pdks`` hash for volare/ciel installs; ``"unknown"`` for
  a PDK install that records no revision, which is itself reported as a
  drift).
- **ngspice_min_major** -- a floor, not an exact match. Newer is allowed
  (and recorded); older is refused.
- **python_min** -- a floor.

A drift is fatal by default -- the CLI should refuse to simulate rather than
record a partial or incomparable result -- with an explicit
``--allow-toolchain-drift`` override for the deliberate case (validating the
repo against a newer PDK), which must be recorded verbatim in the record's
Environment section so a drifted record can never be mistaken for a pinned
one.

EXTENSION POINT: none expected. Add a new pin key here only if a future
block genuinely needs to pin something beyond PDK/ngspice/python (e.g. an
extraction tool version for post-layout records) -- that is a core change,
not a per-block one, since every block sharing this template benefits from
it identically.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .strict_json import no_duplicate_keys

HARNESS_DIR = Path(__file__).resolve().parent
SIM_DIR = HARNESS_DIR.parent

PINS_FILENAME = "toolchain.json"

#: `ngspice-46 : Circuit level simulation program` -> `46`
_NGSPICE_RE = re.compile(r"ngspice-(\d+)")


class PinsError(RuntimeError):
    """The pins file exists but cannot be used."""


@dataclass(frozen=True)
class Drift:
    """One pinned tool whose installed version does not satisfy the pin."""

    tool: str
    pinned: str
    found: str
    detail: str

    def as_dict(self) -> dict:
        return {
            "tool": self.tool,
            "pinned": self.pinned,
            "found": self.found,
            "detail": self.detail,
        }

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.tool}: {self.detail} (pinned {self.pinned}, found {self.found})"


#: The recognised pin keys. Any other key not starting with ``_`` is a typo.
PIN_KEYS = ("pdk_version", "ngspice_min_major", "python_min")

#: ``3``, ``3.11``, ``3.11.4`` -- dotted decimal integers only.
_DOTTED_RE = re.compile(r"[0-9]+(?:\.[0-9]+)*")


def validate_pins(pins: object, source: str = "pins") -> dict:
    """Validate a pin mapping and return it without ``_`` commentary keys.

    ``source`` names the origin (the pins file path, or ``"pins"`` for an
    explicitly supplied mapping) and prefixes every :class:`PinsError`, along
    with the offending field. Absent keys are fine -- every pin is optional --
    but a *present* key must be well-formed, so a pin that is recorded is
    always a pin that is enforced.
    """
    if not isinstance(pins, dict):
        raise PinsError(f"{source} must contain a JSON object")
    out: dict = {}
    for key, value in pins.items():
        if not isinstance(key, str):
            raise PinsError(f"{source}: pin key {key!r} must be a string")
        if key.startswith("_"):
            continue
        if key not in PIN_KEYS:
            raise PinsError(
                f"{source}: unknown pin key {key!r} (expected one of "
                f"{', '.join(PIN_KEYS)}; prefix a comment key with '_')"
            )
        out[key] = value

    if "pdk_version" in out:
        value = out["pdk_version"]
        if not isinstance(value, str) or not value.strip():
            raise PinsError(
                f"{source}: field 'pdk_version' must be a nonblank string, got {value!r}"
            )
    if "ngspice_min_major" in out:
        value = out["ngspice_min_major"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise PinsError(
                f"{source}: field 'ngspice_min_major' must be a positive integer, "
                f"got {value!r}"
            )
    if "python_min" in out:
        value = out["python_min"]
        if not isinstance(value, str) or not _DOTTED_RE.fullmatch(value):
            raise PinsError(
                f"{source}: field 'python_min' must be a dotted numeric version "
                f"such as \"3.11\", got {value!r}"
            )
    return out


def load_pins(sim_dir: Path | None = None) -> dict:
    """Read and validate ``sim/toolchain.json``.

    A missing file (or one holding only ``_`` commentary) means "nothing is
    pinned" and is reported as such rather than silently behaving like a
    passing check. A malformed file raises :class:`PinsError` -- including a
    file that declares the same key twice (``_`` commentary keys included):
    the decoder would otherwise silently keep only the last value (see
    :mod:`strict_json`).
    """
    path = (sim_dir or SIM_DIR) / PINS_FILENAME
    if not path.is_file():
        return {}
    try:
        pins = json.loads(path.read_text(), object_pairs_hook=no_duplicate_keys)
    except ValueError as exc:  # JSONDecodeError, UnicodeDecodeError and the
        # duplicate-key hook are all ValueError subclasses
        raise PinsError(f"{path} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise PinsError(f"{path} cannot be read: {exc}") from exc
    return validate_pins(pins, str(path))


def parse_ngspice_major(version: str) -> int | None:
    """Major version from an ngspice banner, or ``None`` if unrecognisable."""
    match = _NGSPICE_RE.search(version or "")
    return int(match.group(1)) if match else None


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(text).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def check(
    pdk_version: str,
    ngspice_version: str,
    python_version: str,
    pins: dict | None = None,
) -> list[Drift]:
    """Compare the resolved toolchain against the pins. Empty list == OK."""
    pins = load_pins() if pins is None else validate_pins(pins)
    drifts: list[Drift] = []

    pinned_pdk = pins.get("pdk_version")
    if pinned_pdk:
        if pdk_version == "unknown":
            drifts.append(
                Drift(
                    tool="pdk",
                    pinned=pinned_pdk,
                    found="unknown",
                    detail="this PDK install records no version marker, so the "
                    "models it ships cannot be identified",
                )
            )
        elif pdk_version != pinned_pdk:
            drifts.append(
                Drift(
                    tool="pdk",
                    pinned=pinned_pdk,
                    found=pdk_version,
                    detail="different PDK revision — the device models, and therefore "
                    "every measured value, differ from the pinned ones",
                )
            )

    pinned_ngspice = pins.get("ngspice_min_major")
    if pinned_ngspice is not None:
        major = parse_ngspice_major(ngspice_version)
        if major is None:
            drifts.append(
                Drift(
                    tool="ngspice",
                    pinned=f">= {pinned_ngspice}",
                    found=ngspice_version or "unknown",
                    detail="could not parse an ngspice version from the banner",
                )
            )
        elif major < int(pinned_ngspice):
            drifts.append(
                Drift(
                    tool="ngspice",
                    pinned=f">= {pinned_ngspice}",
                    found=str(major),
                    detail="older than the validated engine",
                )
            )

    pinned_python = pins.get("python_min")
    if pinned_python:
        if _version_tuple(python_version) < _version_tuple(pinned_python):
            drifts.append(
                Drift(
                    tool="python",
                    pinned=f">= {pinned_python}",
                    found=python_version,
                    detail="older than the minimum the harness is written against",
                )
            )

    return drifts


def summary(drifts: list[Drift], pins: dict, allowed: bool = False) -> dict:
    """The block recorded in a record's ``environment.toolchain``."""
    pins = validate_pins(pins)
    return {
        "pins": dict(pins),
        "pinned": bool(pins),
        "conforms": not drifts,
        "drift_allowed": bool(allowed),
        "drift": [d.as_dict() for d in drifts],
    }


def format_drifts(drifts: list[Drift]) -> str:
    """The loud, actionable message printed before refusing to simulate."""
    lines = [
        "toolchain drift: the installed tools do not match the versions this",
        "repo's evidence is pinned to (sim/toolchain.json):",
        "",
    ]
    lines += [f"  - {drift}" for drift in drifts]
    lines += [
        "",
        "Nothing was simulated. A record taken against a drifted toolchain is not",
        "comparable with the existing records under sim/, so the run is refused",
        "rather than banked as evidence. Either install the pinned versions, or",
        "re-validate deliberately with --allow-toolchain-drift, which records the",
        "drift in the record's Environment section, then update",
        "sim/toolchain.json in the same change.",
    ]
    return "\n".join(lines)
