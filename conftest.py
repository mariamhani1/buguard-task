"""Root conftest: ensures the project modules (config, crud, ...) are importable
from the tests/ package by keeping the project root on sys.path."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
