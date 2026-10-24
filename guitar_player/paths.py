"""Separate persistent user files from resources extracted by the EXE."""
from pathlib import Path
import sys


def app_root():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else resource_root()


def resource_root():
    return Path(__file__).resolve().parents[1]
