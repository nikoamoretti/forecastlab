# Autopilot outcome-acceptance fixtures

`synthesized_july_2026_cpi_threshold_observations.json` is a **synthesized**
BLS-shaped observation payload. It exists only so the isolated acceptance seed
can freeze a June 2026 year-over-year threshold of 3.0 percent, the latest
published CPI as of the August 5, 2026 seed clock. It is not an official
release, not a live cache entry, and not a production seed.

The calendar used with that payload is a dedicated frozen July 2026 CPI
release at 8:30 a.m. ET on August 12, 2026. It is not the September-oriented
question-selection helper calendar.

The outcome document is the retained official first-release PDF:

`tests/fixtures/official_releases/cpi_08122026.pdf`

That public U.S. government document is the July 2026 CPI first release
(embargoed until 8:30 a.m. ET, August 12, 2026). The headline all-items
year-over-year reading is 3.4 percent, which is greater than the synthesized
3.0 percent threshold (`gt` → outcome 1).
