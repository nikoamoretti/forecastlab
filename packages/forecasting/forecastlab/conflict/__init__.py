"""Country-month political-violence forecasting panel (Phase 1: statistical baselines).

Forecasts UCDP-recorded fatalities from organized violence for every country in the UCDP
GED country list, every month, and scores the forecasts with proper scoring rules. See
docs/CONFLICT_PANEL_V1.md.

Data: UCDP Georeferenced Event Dataset (GED) and UCDP Candidate Events Dataset, Uppsala
Conflict Data Program, licensed CC BY 4.0. Benchmark: ViEWS forecasts from the public
ViEWS API (Uppsala University and the Peace Research Institute Oslo).

This package requires numpy (``uv sync --extra conflict`` or ``--extra dev``). It is not
imported by the API or the worker.
"""
