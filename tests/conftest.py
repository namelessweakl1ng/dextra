"""Pytest config — adds `src/` to sys.path so tests can import dextra."""

import os
import sys

HERE = os.path.dirname(__file__)
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, os.path.abspath(SRC))
