"""Minimal PyInstaller entry point; never read build-time credentials."""

from kickoff_desk.app import main

if __name__ == "__main__":
    raise SystemExit(main())
