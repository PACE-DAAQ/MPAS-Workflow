"""Regression checks for the Forecast restart-stream argument contract."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_forecast_generator_passes_restart_interval_to_runtime():
    source = (ROOT / "initialize/applications/Forecast.py").read_text()
    assert "self['restart interval']," in source


def test_forecast_runtime_validates_and_substitutes_restart_interval():
    source = (ROOT / "bin/Forecast.csh").read_text()
    assert "set rIntOK =" in source
    assert "s@{{restartInterval}}@'${ArgRestartInterval}'@" in source


def test_forecast_stream_template_declares_restart_interval_token():
    templates = list((ROOT / "config/mpas/forecast").glob("streams.*"))
    assert templates
    assert any("{{restartInterval}}" in path.read_text() for path in templates)
