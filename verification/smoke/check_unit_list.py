#!/usr/bin/env python3
"""Drift check for `make test-unit`'s explicit unittest list.

The root Makefile keeps the stdlib unittest list in one `UNIT_TESTS`
variable; the target hands it to this script before running anything.
This check fails (non-zero, naming the file) when a tracked
`test_*.py` in the project tree is a plain-stdlib unittest that the
list does not run -- the failure class from issue #11, one level up
from issue #9's "a test exists that no gate runs": here a gate's
membership was maintained by memory.

Detection is by AST scan of *direct* cocotb imports only: `import
cocotb`, `import cocotb.x`, `from cocotb import y` and `from cocotb.x
import y` (at any nesting depth) mark a file as a cocotb test, which
belongs to the cocotb runner (verification/smoke/Makefile) and is
correctly absent from `UNIT_TESTS`. An *indirect* import -- a test
that reaches cocotb only through a helper module -- is NOT detected;
such a file would be misclassified as stdlib and demanded into
`UNIT_TESTS`, which fails safe (forces a human decision) rather than
silently passing. No such case exists today.

Enumeration is tracked files only (`git ls-files`): a test file not
yet `git add`ed is invisible to this check and gets gated once it is
staged or committed (CI runs `make test-unit` on the full checkout).

`unittest discover` remains rejected (issue #9): discovery would
import the cocotb module outside the cocotb runner. This script never
executes test code -- it parses it.

Usage (paths repo-root relative, straight from `$(UNIT_TESTS)`):
    python3 verification/smoke/check_unit_list.py $(UNIT_TESTS)
"""

from __future__ import annotations

import ast
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

cocotb_class = "cocotb"
stdlib_class = "stdlib"


class CheckError(Exception):
    """A test file could not be read or parsed, so cannot be classified."""


def is_cocotb_source(source: str) -> bool:
    """True if `source` directly imports cocotb anywhere in the file."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "cocotb" or alias.name.startswith("cocotb."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import: a local sibling named
            # "cocotb" is not the cocotb package.
            if node.level == 0 and node.module and (
                node.module == "cocotb" or node.module.startswith("cocotb.")
            ):
                return True
    return False


def classify_file(path: str) -> str:
    """Classify a test file as cocotb or stdlib; raise CheckError if
    it cannot be read or parsed."""
    try:
        source = Path(path).read_text(encoding="utf-8")
        is_cocotb = is_cocotb_source(source)
    except (OSError, SyntaxError, ValueError) as err:
        raise CheckError(f"{path}: {err}") from err
    return cocotb_class if is_cocotb else stdlib_class


def filter_test_files(paths: list[str]) -> list[str]:
    """Keep repo-root-relative paths of tracked test_*.py files,
    dropping `.loom/` scratch trees and non-test modules."""
    kept = []
    for path in paths:
        if path.startswith(".loom/") or "/.loom/" in path:
            continue
        if fnmatch(PurePosixPath(path).name, "test_*.py"):
            kept.append(path)
    return kept


def tracked_test_files(repo_root: Path) -> list[str]:
    """Tracked test_*.py files, repo-root relative, sorted."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    names = [p for p in out.stdout.decode("utf-8").split("\0") if p]
    return sorted(filter_test_files(names))


def _normalize(path: str) -> str:
    return PurePosixPath(path).as_posix()


def check(
    listed: list[str],
    tree: list[str],
    classify=classify_file,
) -> list[str]:
    """Compare the runner's list against the tracked test files.

    Returns one human-readable problem per inconsistency (empty list
    = consistent): a stdlib test absent from the list, a listed file
    missing from the tree, a listed file that imports cocotb, or a
    test file that cannot be classified.
    """
    listed_set = {_normalize(p) for p in listed}
    tree_set = {_normalize(p) for p in tree}
    problems = []
    for path in sorted(tree_set):
        try:
            cls = classify(path)
        except CheckError as err:
            problems.append(f"cannot classify test file: {err}")
            continue
        if path in listed_set:
            if cls == cocotb_class:
                problems.append(
                    f"listed but imports cocotb (belongs to the cocotb"
                    f" runner, not UNIT_TESTS): {path}"
                )
            continue
        if cls == stdlib_class:
            problems.append(
                f"unlisted stdlib unittest -- add it to UNIT_TESTS in the"
                f" root Makefile or make it a cocotb test: {path}"
            )
    for path in sorted(listed_set - tree_set):
        problems.append(
            f"listed in UNIT_TESTS but not tracked in git: {path}"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    repo_root = Path(__file__).resolve().parents[2]
    listed = [_normalize(p) for p in argv]
    tree = tracked_test_files(repo_root)

    def classify(path: str) -> str:
        return classify_file(str(repo_root / path))

    problems = check(listed, tree, classify)
    if problems:
        for problem in problems:
            print(f"check_unit_list: {problem}", file=sys.stderr)
        print(
            f"check_unit_list: {len(problems)} problem(s);"
            f" make test-unit's list is not complete",
            file=sys.stderr,
        )
        return 1
    print(f"check_unit_list: ok ({len(listed)} listed, {len(tree)} tracked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
