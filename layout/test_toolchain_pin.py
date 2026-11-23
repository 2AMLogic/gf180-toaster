#!/usr/bin/env python3
"""Stdlib unittest coverage for layout/toolchain_pin.py (issue #16).

Runs from anywhere (the module under test is found via its own directory):
    python3 layout/test_toolchain_pin.py
    make test-unit

No klt install needed: the klt executable is faked as a small Python script
emulating ``klt --help``'s argparse subcommand block. The real
``layout/toolchain.json`` is read for the pin values, so these tests also
guard that document's filled state.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import toolchain_pin as mod  # noqa: E402

#: A realistic ``klt --help`` subcommand block. Rows sit at the 4-space verb
#: column, the block ends at ``options:`` -- the layout the parser relies on.
_HELP_TEMPLATE = """\
usage: klt [-h] [--version] <command> ...

positional arguments:
  <command>
{command_rows}

options:
  -h, --help            show this help message and exit
"""

_COMMAND_ROWS = """\
    drc                 run a headless DRC deck against a GDSII/OASIS stream
    gen                 run a named layout generator
    version             report the running install's identity
    stats               report area, density, and polygon/vertex counts
"""


def _write_fake_klt(directory: str, command_rows: str) -> str:
    """Create an executable faking ``klt --help``; return its path."""
    path = os.path.join(directory, "klt")
    script = f"""#!{sys.executable}
import sys
if "--help" in sys.argv[1:]:
    print({_HELP_TEMPLATE.format(command_rows=command_rows)!r}, end="")
    sys.exit(0)
sys.exit(0)
"""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(script)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return path


class ToolchainPinTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fake_dir = self._tmp.name
        self._saved_cwd = os.getcwd()
        self.addCleanup(os.chdir, self._saved_cwd)

    # -- the real pin document -----------------------------------------

    def test_real_pin_is_filled(self) -> None:
        """layout/toolchain.json parses and carries no FILL placeholder."""
        pin = mod.load_pin()
        self.assertIsInstance(pin["klt_required_commands"], list)
        self.assertTrue(pin["klt_required_commands"])
        for field in (
            "klt_install",
            "klt_last_verified_commit",
            "klayout_package_min",
        ):
            value = pin[field]
            self.assertIsInstance(value, str, field)
            self.assertTrue(value.strip(), field)
            self.assertNotIn(mod.FILL_MARKER, value, field)

    def test_repo_root_finds_the_pin_document(self) -> None:
        # Default start (this module's own directory) resolves to the repo
        # holding layout/toolchain.json -- what every runner relies on.
        root = mod.repo_root()
        self.assertTrue(os.path.isfile(os.path.join(root, mod.TOOLCHAIN_JSON)))

    def test_repo_root_outside_a_repo_raises(self) -> None:
        with self.assertRaises(mod.ToolchainError):
            mod.repo_root(self.fake_dir)

    # -- require_commands -----------------------------------------------

    def test_missing_command_raises_naming_command_and_install_line(self) -> None:
        """The loud failure: missing verb + the pin's install line.

        The acceptance criterion for issue #16: a missing required command
        must fail naming the command and the install line that fixes it.
        """
        fake = _write_fake_klt(self.fake_dir, "    drc                 run DRC\n")
        pin = {
            "klt_install": "klayout-tools==0.9.2",
            "klt_required_commands": ["drc", "gen"],
            "klt_last_verified_commit": "0" * 40,
        }
        with self.assertRaises(mod.ToolchainError) as ctx:
            mod.require_commands(pin, klt=fake)
        message = str(ctx.exception)
        self.assertIn("gen", message)  # the missing command, by name
        self.assertIn("klayout-tools==0.9.2", message)  # the install line
        self.assertIn(mod.TOOLCHAIN_JSON, message)  # where the pin lives

    def test_all_commands_present_passes(self) -> None:
        fake = _write_fake_klt(self.fake_dir, _COMMAND_ROWS)
        pin = {
            "klt_install": "klayout-tools==0.9.2",
            "klt_required_commands": ["drc", "gen", "version"],
            "klt_last_verified_commit": "0" * 40,
        }
        checked = mod.require_commands(pin, klt=fake)
        self.assertEqual(checked, ["drc", "gen", "version"])

    def test_extra_command_reported_like_a_pinned_one(self) -> None:
        fake = _write_fake_klt(self.fake_dir, "    drc                 run DRC\n")
        pin = {
            "klt_install": "klayout-tools==0.9.2",
            "klt_required_commands": ["drc"],
            "klt_last_verified_commit": "0" * 40,
        }
        with self.assertRaises(mod.ToolchainError) as ctx:
            mod.require_commands(pin, ("extract",), klt=fake)
        self.assertIn("extract", str(ctx.exception))

    def test_unparsed_help_refuses_rather_than_passes(self) -> None:
        """A help text that parses to nothing must fail loudly.

        A formatting change upstream must never read as the pin being
        satisfied on an install whose commands were not actually seen.
        """
        path = os.path.join(self.fake_dir, "klt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                f"#!{sys.executable}\n"
                "import sys\n"
                "print('klt: no subcommands today')\n"
                "sys.exit(0)\n"
            )
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        pin = {
            "klt_install": "klayout-tools==0.9.2",
            "klt_required_commands": ["drc"],
            "klt_last_verified_commit": "0" * 40,
        }
        with self.assertRaises(mod.ToolchainError) as ctx:
            mod.require_commands(pin, klt=path)
        self.assertIn("refusing to report the pin as satisfied", str(ctx.exception))

    def test_unrunnable_klt_raises(self) -> None:
        pin = {
            "klt_install": "klayout-tools==0.9.2",
            "klt_required_commands": ["drc"],
            "klt_last_verified_commit": "0" * 40,
        }
        with self.assertRaises(mod.ToolchainError):
            mod.require_commands(pin, klt=os.path.join(self.fake_dir, "absent"))

    # -- load_pin guards -------------------------------------------------

    def test_fill_placeholder_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pin_path = os.path.join(tmp, "layout", "toolchain.json")
            os.makedirs(os.path.dirname(pin_path))
            with open(pin_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "klt_install": "<!-- FILL: choose a pin -->",
                        "klt_required_commands": ["drc"],
                        "klt_last_verified_commit": "0" * 40,
                    },
                    handle,
                )
            with self.assertRaises(mod.ToolchainError) as ctx:
                mod.load_pin(tmp)
            self.assertIn("klt_install", str(ctx.exception))
            self.assertIn("placeholder", str(ctx.exception))

    # -- CLI -----------------------------------------------------------

    def test_cli_check_missing_command_exits_1(self) -> None:
        """`python3 layout/toolchain_pin.py --check --klt <fake>` exit path."""
        fake = _write_fake_klt(self.fake_dir, "    drc                 run DRC\n")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = mod.main(["--check", "--klt", fake])
        self.assertEqual(code, 1)
        message = stderr.getvalue()
        self.assertIn("error:", message)
        # names a required command the fake lacks (the real pin includes
        # more than drc) and the real install line from the real pin file
        self.assertIn("missing required command", message)
        self.assertIn("klayout-tools==", message)

    def test_cli_check_passing_install_exits_0(self) -> None:
        pin = mod.load_pin()
        rows = "".join(
            f"    {name}{' ' * max(1, 21 - len(name))}stub row\n"
            for name in pin["klt_required_commands"]
        )
        fake = _write_fake_klt(self.fake_dir, rows)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = mod.main(["--check", "--klt", fake])
        self.assertEqual(code, 0)
        self.assertIn("commands ok:", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
