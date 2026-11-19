#!/usr/bin/env python3
"""Stdlib unittest coverage for record_evidence.py's verdict/record behavior.

Runs from anywhere (the module under test is found via its own directory):
    python3 verification/smoke/test_record_evidence.py
    python3 -m unittest verification/smoke/test_record_evidence.py

No cocotb or iverilog needed; git is used only for the record-id suffix.
Record ids are frozen to a fixed timestamp so tests are deterministic.
"""

from __future__ import annotations

import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import record_evidence as mod  # noqa: E402

_FROZEN = datetime.datetime(2026, 11, 19, 6, 0, 0, tzinfo=datetime.timezone.utc)


class _FrozenDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ARG003 - signature mirrors datetime.now
        return _FROZEN


def _results_xml(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuite name="cocotb" tests="1">\n'
        + body
        + "</testsuite>\n"
    )


PASS_XML = _results_xml(
    '  <testcase classname="test_smoke_counter" name="test_counts_up"'
    ' time="0.001"/>\n'
)
FAIL_XML = _results_xml(
    '  <testcase classname="test_smoke_counter" name="test_counts_up"'
    ' time="0.001">\n'
    '    <failure message="count did not advance"'
    ' type="AssertionError">count stuck</failure>\n'
    "  </testcase>\n"
)
EMPTY_XML = _results_xml("")


class RecordEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.evidence = root / "evidence"
        self.results = root / "results.xml"
        patchers = [
            mock.patch.object(mod, "RESULTS_FILE", self.results),
            mock.patch.object(mod, "EVIDENCE_DIR", self.evidence),
            mock.patch.object(mod, "SMOKE_DIR", root),
            mock.patch.object(mod._dt, "datetime", _FrozenDatetime),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _expected_record_id(self) -> str:
        return f"20261119-060000-{mod.git_short_sha()}"

    def _write_results(self, xml: str) -> None:
        self.results.write_text(xml)

    def test_pass_writes_pass_record(self) -> None:
        self._write_results(PASS_XML)
        self.assertEqual(mod.main(), 0)
        record = json.loads(
            (self.evidence / f"{self._expected_record_id()}.json").read_text()
        )
        self.assertEqual(record["verdict"], "pass")
        self.assertEqual(record["results"]["failed"], 0)
        self.assertEqual(record["results"]["failures"], [])
        self.assertEqual(record["experiment"], "verification/smoke")

    def test_failure_writes_fail_record_then_exits_1(self) -> None:
        self._write_results(FAIL_XML)
        self.assertEqual(mod.main(), 1)
        record = json.loads(
            (self.evidence / f"{self._expected_record_id()}.json").read_text()
        )
        self.assertEqual(record["verdict"], "fail")
        self.assertEqual(record["results"]["failed"], 1)
        self.assertEqual(record["results"]["failures"], ["test_counts_up"])

    def test_missing_results_file_exits_1_without_record(self) -> None:
        self.assertEqual(mod.main(), 1)
        self.assertEqual(list(self.evidence.glob("*.json")), [])

    def test_empty_results_file_exits_1_without_record(self) -> None:
        self._write_results(EMPTY_XML)
        self.assertEqual(mod.main(), 1)
        self.assertEqual(list(self.evidence.glob("*.json")), [])

    def test_existing_record_is_never_overwritten(self) -> None:
        self._write_results(FAIL_XML)
        self.assertEqual(mod.main(), 1)
        out = self.evidence / f"{self._expected_record_id()}.json"
        sentinel = '{"sentinel": true}\n'
        out.write_text(sentinel)  # simulate an already-recorded same-second run
        self.assertEqual(mod.main(), 1)  # refuses, still non-zero
        self.assertEqual(out.read_text(), sentinel)


if __name__ == "__main__":
    unittest.main(buffer=True)
