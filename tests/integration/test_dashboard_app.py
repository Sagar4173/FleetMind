from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.integration
def test_streamlit_dashboard_renders_without_exception(tiny_persisted, monkeypatch) -> None:
    paths = tiny_persisted.paths
    assert paths is not None
    monkeypatch.setenv("FLEETMIND_DB", str(paths.warehouse_path))
    from streamlit.testing.v1 import AppTest

    app_path = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"
    app = AppTest.from_file(app_path, default_timeout=60)
    app.run()
    assert [str(item.value) for item in app.exception] == []
    assert [item.value for item in app.title] == ["FleetMind Control Tower"]
    assert len(app.tabs) == 5
    assert len(app.metric) >= 6
    assert len(app.get("plotly_chart")) >= 5
