"""Fail on Python errors and increases in the recorded legacy style debt."""

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

BASELINE = Path("ci_tools/pylint-baseline.json")
ARTIFACTS = Path("artifacts")


def tracked_python_files():
    """Use Git's file inventory, never installed dependencies or generated code."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*.py"],
        check=True, capture_output=True, text=True,
    )
    return sorted(set(filter(None, result.stdout.split("\0"))))


def findings():
    """Run every Pylint check; enforcement is based on structured diagnostics."""
    result = subprocess.run(
        [sys.executable, "-m", "pylint", "--output-format=json", "--reports=n", "--score=n",
         "--exit-zero", *tracked_python_files()], capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    rows = json.loads(result.stdout)
    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / "pylint.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return rows


def style_counts(rows):
    """Ignore line numbers so harmless code movement does not change the budget."""
    return Counter(f"{row['path']}::{row['symbol']}" for row in rows if row["type"] not in {"error", "fatal"})


def check(rows, update=False):
    """Errors never get a baseline allowance; style debt can only decrease."""
    errors = [row for row in rows if row["type"] in {"error", "fatal"}]
    for row in errors:
        print(f"{row['path']}:{row['line']}: {row['message-id']} {row['message']}")
    counts = style_counts(rows)
    if update:
        if errors:
            raise RuntimeError("Fix Python errors before recording a style baseline.")
        BASELINE.write_text(json.dumps(dict(sorted(counts.items())), indent=2) + "\n", encoding="utf-8")
        print(f"Recorded {sum(counts.values())} existing style diagnostics; no Python errors.")
        return True
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    increases = {
        key: value - baseline.get(key, 0)
        for key, value in counts.items() if value > baseline.get(key, 0)
    }
    for key, increase in sorted(increases.items()):
        print(f"New lint debt: {key}: +{increase}; see artifacts/pylint.json")
    print(f"Pylint: {len(errors)} errors, {sum(counts.values())} style diagnostics, "
          f"{len(increases)} increased budgets.")
    return not errors and not increases


def main():
    """CLI entry point. Baseline updates are explicit and never done by CI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()
    return 0 if check(findings(), args.update_baseline) else 1


if __name__ == "__main__":
    sys.exit(main())
