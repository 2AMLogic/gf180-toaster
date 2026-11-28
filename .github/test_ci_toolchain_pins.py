#!/usr/bin/env python3
"""Stdlib unittest coverage for CI's toolchain pin provisioning (issue #30).

`.github/workflows/ci.yml` installs klt from layout/toolchain.json's
``klt_install`` and the gf180mcu PDK at the open_pdks revision inside
sim/toolchain.json's ``pdk_version``. The workflow's "Read toolchain
pins" step reads both fields once and publishes them as ``KLT_INSTALL``
and ``OPEN_PDKS_REV``; the pinned-package pip install, the PDK cache
key and the ciel install consume those variables instead of restating a
pin.

The script under test is the one CI runs: every ``run:`` block is read
straight out of the workflow file and executed with the shell GitHub
Actions uses, against a scratch ``$GITHUB_ENV``, a scratch checkout
holding the two JSON files, and fake ``.venv/bin/pip`` and
``.venv/bin/ciel`` that record their argv. Expected values come from
the JSON, never from a literal here, so a pin bump needs no edit to
this file.

Runs from anywhere:
    python3 .github/test_ci_toolchain_pins.py
    make test-unit

Needs only python3 and bash; no network and no real install.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
LAYOUT_PIN = Path("layout") / "toolchain.json"
SIM_PIN = Path("sim") / "toolchain.json"

READ_STEP = "Read toolchain pins"
VENV_STEP = "Set up Python venv (cocotb + ciel + klt)"
PDK_STEP = "Install gf180mcu PDK (pinned revision)"

#: What the read step publishes to ``$GITHUB_ENV``; the consumers read these.
KLT_INSTALL = "KLT_INSTALL"
OPEN_PDKS_REV = "OPEN_PDKS_REV"
PUBLISHED = (KLT_INSTALL, OPEN_PDKS_REV)

_RUN = re.compile(r"^(?P<indent> *)run: \|\s*$")
_CACHE_KEY = re.compile(r"^ *key: (gf180mcu-open_pdks-.+) *$", re.MULTILINE)
_ENV_EXPRESSION = re.compile(r"\$\{\{ env\.(\w+) \}\}")
_BASH = ("bash", "--noprofile", "--norc", "-eo", "pipefail", "-c")
_FAKE_TOOL = '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$FAKE_ARGV"\n'
_KEEP = object()


def run_scripts(workflow: str) -> dict[str, str | None]:
    """Every ``run: |`` block in ``workflow``, keyed by its step's name.

    Line-based on purpose -- ``make test-unit`` is stdlib-only -- and it
    follows the rule a YAML reader applies to a literal block scalar: the
    script is every following line indented deeper than its ``run:``, and
    the block ends at the first other non-blank line.
    """
    lines = workflow.splitlines()
    scripts: dict[str, str | None] = {}
    index = 0
    while index < len(lines):
        match = _RUN.match(lines[index])
        if match is None:
            index += 1
            continue
        depth = len(match.group("indent"))
        name: str | None = None
        for previous in reversed(lines[:index]):
            if previous.startswith(" " * (depth - 2) + "- "):
                header = previous.strip()
                if header.startswith("- name: "):
                    name = header[len("- name: ") :]
                break
        index += 1
        body: list[str] = []
        while index < len(lines):
            line = lines[index]
            if line.strip() and len(line) - len(line.lstrip(" ")) <= depth:
                break
            body.append(line)
            index += 1
        scripts[name] = textwrap.dedent("\n".join(body)) + "\n"
    return scripts


def json_pin(root: Path, relpath: Path) -> dict:
    """The pin document at ``root / relpath`` as a dict (the test's own read)."""
    return json.loads((root / relpath).read_text(encoding="utf-8"))


class CiToolchainPinsTest(unittest.TestCase):
    """AC: CI's pip argument, cache key and ciel argument follow the JSON pins."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._checkouts = 0
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        self.scripts = run_scripts(self.workflow)

    # -- scaffolding -----------------------------------------------------

    def _script(self, step: str) -> str:
        self.assertIn(
            step,
            self.scripts,
            f"{WORKFLOW.name} has no run step named {step!r} -- renamed? "
            "update this test",
        )
        script = self.scripts[step]
        self.assertTrue(script and script.strip(), f"step {step!r} has an empty script")
        return script or ""

    def _checkout(
        self, *, klt_install: object = _KEEP, pdk_version: object = _KEEP
    ) -> Path:
        """A scratch checkout holding copies of the two pin files.

        Either keyword replaces its field in the copy (``None`` deletes
        it). The real files are only ever read.
        """
        self._checkouts += 1
        root = Path(self._tmp.name) / f"checkout-{self._checkouts}"
        for relpath, field, value in (
            (LAYOUT_PIN, "klt_install", klt_install),
            (SIM_PIN, "pdk_version", pdk_version),
        ):
            target = root / relpath
            target.parent.mkdir(parents=True)
            shutil.copy2(ROOT / relpath, target)
            if value is _KEEP:
                continue
            pin = json.loads(target.read_text(encoding="utf-8"))
            if value is None:
                del pin[field]
            else:
                pin[field] = value
            target.write_text(json.dumps(pin), encoding="utf-8")
        return root

    def _environment(self, root: Path) -> dict[str, str]:
        env = {
            name: value for name, value in os.environ.items() if name not in PUBLISHED
        }
        env["GITHUB_ENV"] = str(root / "github_env")
        return env

    def _bash(
        self, script: str, root: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*_BASH, script],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def _published(self, root: Path) -> dict[str, str]:
        env_file = root / "github_env"
        if not env_file.exists():
            return {}
        lines = env_file.read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines)

    def _read_pins(self, root: Path) -> dict[str, str]:
        """Run the read step in ``root`` and return what it published."""
        completed = self._bash(self._script(READ_STEP), root, self._environment(root))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        published = self._published(root)
        self.assertEqual(sorted(published), sorted(PUBLISHED))
        return published

    def _render(self, key: str, pins: dict[str, str]) -> str:
        """The cache key as the runner evaluates it, from ``pins``."""
        for name in _ENV_EXPRESSION.findall(key):
            self.assertIn(
                name,
                pins,
                f"the cache key names {{{{ env.{name} }}}}, which the read "
                "step does not publish",
            )
        return _ENV_EXPRESSION.sub(lambda match: pins[match.group(1)], key)

    def _resolve(self, root: Path) -> dict[str, object]:
        """What the pip install, cache key and ciel install become in ``root``."""
        pins = self._read_pins(root)
        env = dict(self._environment(root), **pins)
        argv: dict[str, Path] = {}
        for tool in ("pip", "ciel"):
            fake = root / ".venv" / "bin" / tool
            fake.parent.mkdir(parents=True, exist_ok=True)
            fake.write_text(_FAKE_TOOL, encoding="utf-8")
            fake.chmod(0o755)
            argv[tool] = root / f"{tool}.argv"
        install = [
            line
            for line in self._script(VENV_STEP).splitlines()
            if line.startswith(".venv/bin/pip install ") and "--upgrade" not in line
        ]
        self.assertEqual(len(install), 1, "the venv step's pinned-package install line")
        for script, tool in ((install[0], "pip"), (self._script(PDK_STEP), "ciel")):
            completed = self._bash(script, root, dict(env, FAKE_ARGV=str(argv[tool])))
            self.assertEqual(completed.returncode, 0, completed.stderr)
        keys = _CACHE_KEY.findall(self.workflow)
        self.assertEqual(len(keys), 1, "the PDK cache key line")
        return {
            "pip": argv["pip"].read_text(encoding="utf-8").splitlines(),
            "cache_key": self._render(keys[0], pins),
            "ciel": argv["ciel"].read_text(encoding="utf-8").splitlines(),
        }

    # -- the issue's checks -----------------------------------------------

    def test_consumers_resolve_to_the_json_pins(self) -> None:
        """AC: with the committed pins, all three consumers carry the JSON values."""
        root = self._checkout()
        klt_install = json_pin(root, LAYOUT_PIN)["klt_install"]
        revision = json_pin(root, SIM_PIN)["pdk_version"].split()[1]
        resolved = self._resolve(root)
        self.assertEqual(resolved["pip"].count(klt_install), 1)
        self.assertEqual(resolved["cache_key"], f"gf180mcu-open_pdks-{revision}")
        self.assertIn(revision, resolved["ciel"])

    def test_pin_bump_reaches_every_consumer(self) -> None:
        """AC: the temp-checkout check -- bump both pins, all three follow."""
        revision = "b" * 40
        klt_install = f"klayout-tools @ git+https://example.invalid/klt@{'c' * 40}"
        root = self._checkout(
            klt_install=klt_install, pdk_version=f"open_pdks {revision}"
        )
        resolved = self._resolve(root)
        self.assertEqual(resolved["pip"].count(klt_install), 1)
        self.assertEqual(resolved["cache_key"], f"gf180mcu-open_pdks-{revision}")
        self.assertIn(revision, resolved["ciel"])

    def test_workflow_restates_no_pin(self) -> None:
        """AC: the drift the issue removes -- no copy of a JSON pin in the workflow."""
        klt_install = json_pin(ROOT, LAYOUT_PIN)["klt_install"]
        revision = json_pin(ROOT, SIM_PIN)["pdk_version"].split()[1]
        self.assertNotIn(klt_install, self.workflow)
        self.assertNotIn(revision, self.workflow)
        self.assertNotRegex(self.workflow, r"klayout-tools==")
        self.assertNotRegex(self.workflow, r"\b[0-9a-f]{40}\b")

    def test_read_step_fails_loudly_on_a_pin_it_cannot_read(self) -> None:
        """AC: a malformed or missing pin fails the step instead of provisioning."""
        for label, checkout in (
            ("pdk_version without a revision", {"pdk_version": "open_pdks"}),
            ("blank pdk_version", {"pdk_version": "   "}),
            ("missing klt_install", {"klt_install": None}),
        ):
            with self.subTest(label):
                root = self._checkout(**checkout)
                completed = self._bash(
                    self._script(READ_STEP), root, self._environment(root)
                )
                self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main(buffer=True)
