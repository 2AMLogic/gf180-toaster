#!/usr/bin/env python3
"""Shared klt toolchain-pin check for every ``layout/`` runner (issue #16).

``layout/toolchain.json``'s own comment block asks for exactly this: "Keep ONE
shared implementation (conventionally ``layout/toolchain_pin.py``) that every
DRC/LVS/extraction runner imports, rather than re-deriving this check per
runner -- otherwise the runners can drift apart on what they check." The
pattern is taken from 2AMLogic/sg13g2-sat-rx's ``layout/toolchain_pin.py``
(not redesigned here); the PDK-asset half of that sibling's module is
deliberately absent, because gf180-toaster's runners hand PDK resolution to
``klt``'s own ``--pdk`` flag instead of resolving install paths themselves.

UPSTREAM ISSUE THAT DELETES THIS MODULE: 2AMLogic/klayout-tools#7331
("consumers hand-roll a required-commands / pin preflight; offer it as a klt
verb"). Once klt ships such a verb, every runner here should call it and this
file goes away. Until then this module is the one shared implementation, so
the check cannot drift between runners.

What it checks, and why each is a *loud* failure rather than a skipped check:

``load_pin()``
    ``layout/toolchain.json`` parses, and no field every runner depends on is
    still a bootstrap ``<!-- FILL: ... -->`` placeholder. An unfilled pin
    document means every runner's evidence would cite a pin nobody chose.

``load_pdk_version()``
    ``sim/toolchain.json`` parses and carries a non-empty ``pdk_version``
    -- the open_pdks revision both halves share, pinned once and restated
    nowhere. Layout runners assert DRC-report provenance against it
    (issue #20); a missing pin is a loud failure, never "nothing to
    compare".

``require_commands()``
    every entry of ``klt_required_commands`` appears in ``klt --help``'s
    subcommand list. A missing command means the runner's evidence would be
    silently narrower than the pin promises.

Standard library only, so a runner can import it before any third-party
package is known to be installed. Nothing here runs ``klt``'s *checks* -- it
only establishes that the pinned tool is the one in front of us.

Usage from a runner::

    from toolchain_pin import load_pin, require_commands

    pin = load_pin()
    require_commands(pin, klt="/path/to/klt")

Usage as a CLI (the ``--check`` convention every runner also exposes)::

    python3 layout/toolchain_pin.py --check --klt /path/to/klt
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

#: Repository-relative location of the pin document this module reads.
TOOLCHAIN_JSON = "layout/toolchain.json"

#: Repository-relative location of the analog half's toolchain pin, whose
#: ``pdk_version`` (the open_pdks revision) layout runners assert their
#: DRC-report provenance against (issue #20).
SIM_TOOLCHAIN_JSON = "sim/toolchain.json"

#: Marker the bootstrap starter used for unfilled fields (see that file's
#: comment block). A pin document still carrying one is not a pin yet.
FILL_MARKER = "<!-- FILL"


class ToolchainError(Exception):
    """A pinned tool or command is missing or the pin document is unfilled.

    Runners turn this into a clean stderr message plus a non-zero exit, never
    a traceback and never a silently-skipped check.
    """


def repo_root(start: str | None = None) -> str:
    """The repository root, found by walking up from ``start`` to the nearest
    directory holding ``layout/toolchain.json``.

    Deliberately not ``git rev-parse``: a runner must work in an exported
    source tree with no ``.git``, and the pin document is the thing we
    actually need.
    """
    here = os.path.abspath(start or os.path.dirname(os.path.abspath(__file__)))
    while True:
        if os.path.isfile(os.path.join(here, TOOLCHAIN_JSON)):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            raise ToolchainError(
                f"could not find {TOOLCHAIN_JSON} in any ancestor of "
                f"{os.path.abspath(start or __file__)}"
            )
        here = parent


def load_pin(root: str | None = None) -> dict:
    """Parse ``layout/toolchain.json`` and return it.

    Raises :class:`ToolchainError` when the file is unreadable, is not valid
    JSON, still carries a bootstrap ``FILL`` placeholder in any non-comment
    field, or lacks a field every runner depends on -- rather than letting a
    ``KeyError`` surface later from inside a runner.
    """
    root = root or repo_root()
    path = os.path.join(root, TOOLCHAIN_JSON)
    try:
        with open(path, encoding="utf-8") as handle:
            pin = json.load(handle)
    except OSError as exc:
        raise ToolchainError(f"cannot read {TOOLCHAIN_JSON}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ToolchainError(f"{TOOLCHAIN_JSON} is not valid JSON: {exc}") from exc

    for field in ("klt_install", "klt_required_commands", "klt_last_verified_commit"):
        if field not in pin:
            raise ToolchainError(f"{TOOLCHAIN_JSON} is missing {field!r}")
        if isinstance(pin[field], str) and FILL_MARKER in pin[field]:
            raise ToolchainError(
                f"{TOOLCHAIN_JSON}: {field!r} is still the bootstrap "
                "placeholder -- fill the pin in before running any layout/"
                " runner (see that file's comment block)"
            )
    if not isinstance(pin["klt_required_commands"], list):
        raise ToolchainError(
            f"{TOOLCHAIN_JSON}: klt_required_commands must be a JSON array"
        )
    return pin


def load_pdk_version(root: str | None = None) -> str:
    """Return ``sim/toolchain.json``'s ``pdk_version`` -- the pinned
    open_pdks revision layout evidence is asserted against (issue #20).

    The analog and layout halves share one PDK install, so the revision is
    pinned once in sim/toolchain.json; restating it in
    layout/toolchain.json would add a drift surface. Raises
    :class:`ToolchainError` when the file is unreadable, is not valid
    JSON, or carries no non-empty ``pdk_version`` -- a missing pin must
    fail loudly rather than read as "nothing to compare".
    """
    root = root or repo_root()
    path = os.path.join(root, SIM_TOOLCHAIN_JSON)
    try:
        with open(path, encoding="utf-8") as handle:
            sim_pin = json.load(handle)
    except OSError as exc:
        raise ToolchainError(f"cannot read {SIM_TOOLCHAIN_JSON}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ToolchainError(
            f"{SIM_TOOLCHAIN_JSON} is not valid JSON: {exc}"
        ) from exc
    version = sim_pin.get("pdk_version")
    if not isinstance(version, str) or not version.strip():
        raise ToolchainError(
            f"{SIM_TOOLCHAIN_JSON}: pdk_version is missing or empty -- pin "
            "the open_pdks revision there; layout DRC provenance is "
            "asserted against it (issue #20)"
        )
    return version


def klt_subcommands(klt: str) -> set[str]:
    """Every subcommand ``klt --help`` advertises for the install at ``klt``.

    Parsed out of the help text's subcommand block, which is the only
    machine-readable list a `klt` build exposes without running a verb (the
    gap 2AMLogic/klayout-tools#7324/#7331 exist to close). A help text that
    parses to *nothing* raises rather than reporting the pin satisfied -- a
    formatting change must fail loudly, never read as "install missing
    commands".
    """
    try:
        proc = subprocess.run(
            [klt, "--help"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except OSError as exc:
        raise ToolchainError(f"cannot run {klt!r}: {exc}") from exc
    if proc.returncode != 0:
        raise ToolchainError(
            f"{klt} --help exited {proc.returncode}: {proc.stderr.strip()[:400]}"
        )

    found: set[str] = set()
    in_block = False
    for line in proc.stdout.splitlines():
        if line.startswith("positional arguments:"):
            in_block = True
            continue
        if in_block:
            if line.startswith("options:") or line.startswith("optional arguments:"):
                break
            stripped = line.strip()
            # Subcommand rows sit at the verb column (4-space indent) and
            # start with the verb; the "<command>" header and continuation
            # lines of long descriptions are indented differently.
            if not stripped or stripped.startswith("<"):
                continue
            indent = len(line) - len(line.lstrip())
            if indent == 4:
                found.add(stripped.split()[0])
    if not found:
        raise ToolchainError(
            f"could not parse any subcommand out of {klt} --help; refusing to "
            "report the pin as satisfied on an unparsed help text"
        )
    return found


def require_commands(
    pin: dict, extra: tuple[str, ...] = (), *, klt: str = "klt"
) -> list[str]:
    """Assert the install at ``klt`` has every pinned command, plus ``extra``.

    ``extra`` is for a runner that needs a verb the pin does not list yet --
    it is reported in the error message exactly like a pinned one, so the fix
    (add it to ``klt_required_commands``) is visible from the failure.

    Returns the sorted list of commands that were checked.
    """
    want = sorted(set(pin["klt_required_commands"]) | set(extra))
    have = klt_subcommands(klt)
    missing = [command for command in want if command not in have]
    if missing:
        raise ToolchainError(
            "the klt install at {klt!r} is missing required command(s): {missing}.\n"
            "  {relpath} pins klt_install = {install!r}.\n"
            "  Install that pin into a throwaway environment (never the host "
            "tool):\n"
            "    uv venv /tmp/klt-pin && uv pip install --python "
            "/tmp/klt-pin/bin/python {install!r}\n"
            "  then re-run with --klt /tmp/klt-pin/bin/klt.".format(
                klt=klt,
                missing=", ".join(missing),
                relpath=TOOLCHAIN_JSON,
                install=pin["klt_install"],
            )
        )
    return want


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="check this repo's klt toolchain pin against an install"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="run every check and report; exit 1 on the first failure",
    )
    parser.add_argument(
        "--klt", default="klt", help="klt executable to check (default: klt)"
    )
    parser.add_argument(
        "--extra",
        default="",
        help="comma-separated extra commands to require beyond the pin",
    )
    args = parser.parse_args(argv)

    try:
        pin = load_pin()
        extra = tuple(name for name in args.extra.split(",") if name)
        checked = require_commands(pin, extra, klt=args.klt)
    except ToolchainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"klt_install:             {pin['klt_install']}")
    print(f"klt_last_verified_commit: {pin['klt_last_verified_commit']}")
    print(f"commands ok:             {', '.join(checked)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
