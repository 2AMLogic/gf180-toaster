#!/usr/bin/env python3
"""Turn cocotb's results.xml into an append-only digital smoke evidence record.

Run after `make` in verification/smoke/ (the repo-root Makefile's smoke-digital
target does this). Writes one JSON record per invocation to
evidence/<record-id>.json, mirroring the analog harness's record-id
convention (<YYYYMMDD>-<HHMMSS>-<short-sha>); existing records are never
edited. A failing run still writes its error record first, then exits
non-zero, mirroring the analog harness; a missing or empty results file
exits non-zero without writing a record.

Stdlib only; no cocotb import needed to run it.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SMOKE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = SMOKE_DIR / "evidence"
RESULTS_FILE = SMOKE_DIR / "results.xml"

_RECORD_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9A-Za-z]+$")


def git_short_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=SMOKE_DIR, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return out or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def tool_version(argv: list[str], pattern: str) -> str:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError:
        return "unknown"
    for line in (out.stdout + out.stderr).splitlines():
        if pattern in line:
            return line.strip()
    return "unknown"


def cocotb_version() -> str:
    """`cocotb-config --version` prints a bare version number, e.g. '1.9.2'."""
    try:
        out = subprocess.run(
            ["cocotb-config", "--version"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return "unknown"
    text = out.stdout.strip()
    return text.splitlines()[0] if text else "unknown"


def parse_results(path: Path) -> dict:
    """cocotb results.xml (JUnit-style, cocotb >= 1.6) -> failure counts.

    A `<testcase>` fails when it carries a `<failure>` or `<error>` child;
    a skipped test carries `<skipped>` and counts as neither.
    """
    root = ET.parse(path).getroot()
    tests = root.findall(".//testcase")
    failures = [
        t.get("name", "?")
        for t in tests
        if t.find("failure") is not None or t.find("error") is not None
    ]
    skipped = sum(1 for t in tests if t.find("skipped") is not None)
    return {
        "total": len(tests),
        "passed": len(tests) - len(failures) - skipped,
        "failed": len(failures),
        "skipped": skipped,
        "failures": failures,
    }


def main() -> int:
    if not RESULTS_FILE.is_file():
        print(f"error: {RESULTS_FILE} not found -- run the cocotb smoke first", file=sys.stderr)
        return 1
    results = parse_results(RESULTS_FILE)
    if results["total"] == 0:
        print(f"error: {RESULTS_FILE} contains no <test> entries", file=sys.stderr)
        return 1

    now = _dt.datetime.now(_dt.timezone.utc)
    short_sha = git_short_sha()
    record_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{short_sha}"
    if not _RECORD_ID_RE.match(record_id):  # path-safety belt, mirrors report.py
        print(f"error: minted record id {record_id!r} is malformed", file=sys.stderr)
        return 1

    record = {
        "record_id": record_id,
        "recorded_utc": now.isoformat(timespec="seconds"),
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=SMOKE_DIR, capture_output=True, text=True, check=False,
        ).stdout.strip() or "unknown",
        "experiment": "verification/smoke",
        "results": results,
        "verdict": "fail" if results["failed"] else "pass",
        "tools": {
            "python": sys.version.split()[0],
            "cocotb": cocotb_version(),
            "iverilog": tool_version(["iverilog", "-V"], "Icarus Verilog"),
        },
    }

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE_DIR / f"{record_id}.json"
    if out.exists():  # append-only: never overwrite an existing record
        print(f"error: evidence record {out.name} already exists", file=sys.stderr)
        return 1
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(f"digital smoke evidence: {out.relative_to(SMOKE_DIR.parent.parent)} "
          f"({results['passed']}/{results['total']} tests passed, verdict: {record['verdict']})")
    if results["failed"]:
        print(f"error: {results['failed']} cocotb test(s) failed: {results['failures']}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
