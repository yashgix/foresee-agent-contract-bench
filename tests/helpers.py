import pytest

from bench import snapshot


def load_snapshot() -> snapshot.Snapshot:
    """Foresee's documents aren't redistributed here: skip the module until scripts/fetch_snapshot.py has run."""
    try:
        return snapshot.load()
    except (FileNotFoundError, ValueError) as exc:
        pytest.skip(f"Foresee snapshot unavailable ({exc}); run scripts/fetch_snapshot.py", allow_module_level=True)
