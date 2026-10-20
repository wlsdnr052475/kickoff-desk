"""Launch the real GUI in isolated Mock mode with a bounded runtime."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--executable", type=Path, help="Test a packaged executable instead of Python"
    )
    parser.add_argument(
        "--timeout", type=float, default=30, help="Timeout in seconds (default: 30)"
    )
    options = parser.parse_args()
    if options.timeout <= 0:
        parser.error("--timeout must be positive")
    command = (
        [str(options.executable.resolve())]
        if options.executable is not None
        else [sys.executable, "-m", "kickoff_desk"]
    )
    command.extend(["--mock", "--smoke-test"])
    environment = os.environ.copy()
    environment.pop("FOOTBALL_DATA_API_KEY", None)
    if sys.platform.startswith("linux"):
        environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    with tempfile.TemporaryDirectory(prefix="kickoff-desk-smoke-") as directory:
        environment["LOCALAPPDATA"] = directory
        environment["XDG_DATA_HOME"] = directory
        try:
            result = subprocess.run(command, env=environment, timeout=options.timeout, check=False)
        except subprocess.TimeoutExpired:
            print("GUI smoke check timed out.", file=sys.stderr)
            return 1
        except OSError:
            print("GUI smoke check could not launch the application.", file=sys.stderr)
            return 1
    if result.returncode != 0:
        print(f"GUI smoke check failed with exit code {result.returncode}.", file=sys.stderr)
        return 1
    print("GUI Mock smoke check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
