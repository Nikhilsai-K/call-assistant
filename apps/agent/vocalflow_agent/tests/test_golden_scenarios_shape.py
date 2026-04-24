"""
Smoke-tests the golden scenario file itself — structure must stay valid as
scenarios are edited. The full replay harness (uses real providers) runs in
the `eval` CI job, not unit tests.
"""
import json
from pathlib import Path

SCENARIO_FILE = Path(__file__).parent / "golden_scenarios.json"


def test_scenarios_file_parses_and_has_required_keys():
    data = json.loads(SCENARIO_FILE.read_text())
    assert len(data) >= 10
    for s in data:
        assert "name" in s
        assert "persona" in s


def test_every_scenario_name_is_unique():
    data = json.loads(SCENARIO_FILE.read_text())
    names = [s["name"] for s in data]
    assert len(names) == len(set(names))
