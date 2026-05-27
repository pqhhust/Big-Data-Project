"""Shared pytest configuration and fixtures for BrainWatch tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure scripts/ directory is importable for test_download_eeg_ehr
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
