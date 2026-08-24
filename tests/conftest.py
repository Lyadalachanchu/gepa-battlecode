import sys
from pathlib import Path

# Tests import the repo packages by absolute name from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "slow: runs a real engine match via gradle (seconds to minutes)"
    )
