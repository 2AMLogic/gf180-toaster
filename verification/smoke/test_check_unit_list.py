#!/usr/bin/env python3
"""Stdlib unittest coverage for check_unit_list.py's drift detection.

Runs from anywhere (the module under test is found via its own directory):
    python3 verification/smoke/test_check_unit_list.py
    python3 -m unittest verification/smoke/test_check_unit_list.py

No cocotb or git operations: the comparison logic is driven against
synthetic trees in a temp dir, and the cocotb AST predicate against
inline source strings. Issue #11.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_unit_list as mod  # noqa: E402

STDLIB_SOURCE = "import unittest\n"


class CocotbPredicateTest(unittest.TestCase):
    """AC: both `from cocotb...` and `import cocotb.x` forms detected."""

    def _assert_cocotb(self, source: str) -> None:
        self.assertTrue(mod.is_cocotb_source(source), source)

    def _assert_stdlib(self, source: str) -> None:
        self.assertFalse(mod.is_cocotb_source(source), source)

    def test_import_cocotb_detected(self) -> None:
        self._assert_cocotb("import cocotb\n")

    def test_import_cocotb_dotted_detected(self) -> None:
        self._assert_cocotb("import cocotb.triggers\n")

    def test_from_cocotb_import_detected(self) -> None:
        self._assert_cocotb("from cocotb import test\n")

    def test_from_cocotb_dotted_import_detected(self) -> None:
        self._assert_cocotb("from cocotb.triggers import Timer\n")

    def test_import_inside_function_body_detected(self) -> None:
        self._assert_cocotb(
            "import unittest\n"
            "\n"
            "\n"
            "def helper():\n"
            "    import cocotb\n"
            "    return cocotb\n"
        )

    def test_plain_stdlib_not_detected(self) -> None:
        self._assert_stdlib(STDLIB_SOURCE)

    def test_unittest_from_import_not_detected(self) -> None:
        self._assert_stdlib("from unittest import mock\n")

    def test_similar_module_name_not_detected(self) -> None:
        self._assert_stdlib("import cocotb_utils\n")
        self._assert_stdlib("from cocotb_utils import thing\n")

    def test_relative_import_of_cocotb_not_detected(self) -> None:
        # `from . import cocotb` imports a local sibling module, not the
        # cocotb package -- out of scope by design (docstring).
        self._assert_stdlib("from . import cocotb\n")


class FilterTest(unittest.TestCase):
    """AC: `.loom/` test files ignored; only test_*.py enumerated."""

    def test_loom_paths_excluded(self) -> None:
        self.assertEqual(mod.filter_test_files([".loom/worktrees/issue-1/test_x.py"]), [])

    def test_nested_loom_path_excluded(self) -> None:
        self.assertEqual(
            mod.filter_test_files(["verification/.loom/cache/test_x.py"]), []
        )

    def test_test_underscore_prefix_required(self) -> None:
        self.assertEqual(
            mod.filter_test_files(["sim/harness/testbench.py"]), []
        )

    def test_nested_test_files_kept(self) -> None:
        self.assertEqual(
            mod.filter_test_files(
                ["a/b/test_deep.py", "verification/smoke/test_top.py"]
            ),
            ["a/b/test_deep.py", "verification/smoke/test_top.py"],
        )


class CheckTest(unittest.TestCase):
    """AC: unlisted stdlib -> fail; unlisted cocotb -> pass; listed -> pass."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, source: str) -> str:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        return str(path)

    def test_unlisted_stdlib_test_fails_naming_file(self) -> None:
        path = self._write("verification/x/test_tmp.py", STDLIB_SOURCE)
        problems = mod.check(listed=[], tree=[path])
        self.assertEqual(len(problems), 1)
        self.assertIn("test_tmp.py", problems[0])

    def test_unlisted_cocotb_test_passes(self) -> None:
        path = self._write(
            "verification/x/test_cocotb_thing.py", "import cocotb\n"
        )
        self.assertEqual(mod.check(listed=[], tree=[path]), [])

    def test_listed_stdlib_test_passes(self) -> None:
        path = self._write("verification/x/test_tmp.py", STDLIB_SOURCE)
        self.assertEqual(mod.check(listed=[path], tree=[path]), [])

    def test_listed_but_missing_from_tree_fails(self) -> None:
        problems = mod.check(
            listed=["verification/x/test_gone.py"], tree=[]
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("test_gone.py", problems[0])

    def test_listed_cocotb_test_fails(self) -> None:
        path = self._write(
            "verification/x/test_cocotb_thing.py", "import cocotb\n"
        )
        problems = mod.check(listed=[path], tree=[path])
        self.assertEqual(len(problems), 1)
        self.assertIn("test_cocotb_thing.py", problems[0])

    def test_unparseable_test_fails_rather_than_skipping(self) -> None:
        path = self._write("verification/x/test_broken.py", "def (:\n")
        problems = mod.check(listed=[], tree=[path])
        self.assertEqual(len(problems), 1)
        self.assertIn("test_broken.py", problems[0])


if __name__ == "__main__":
    unittest.main(buffer=True)
