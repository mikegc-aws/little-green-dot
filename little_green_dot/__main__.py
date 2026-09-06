"""Allows `python -m little_green_dot`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
