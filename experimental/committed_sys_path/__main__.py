"""Entrypoint for python -m experimental.committed_sys_path."""

import sys

from experimental.committed_sys_path.cli import main

if __name__ == "__main__":
    sys.exit(main())
