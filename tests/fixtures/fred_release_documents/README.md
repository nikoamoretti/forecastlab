# Official publication documents behind the FRED fast series

Public U.S. government documents, fetched October 3, 2026 with the ForecastLab
public-data user agent. Both paths are allowed by the hosts' robots.txt
(`www.dol.gov/robots.txt` does not disallow `/ui/`; `www.federalreserve.gov`
serves no robots.txt). The original bytes exercise PDF and HTML text extraction,
verbatim quotations, publication dates and the publication-pattern checks.
These are historical test fixtures and are never used as live source cache
entries.

- `dol_ui_data_10012026.pdf`: https://www.dol.gov/ui/data.pdf, the
  Unemployment Insurance Weekly Claims news release embargoed until 8:30 a.m.
  Eastern, Thursday, October 1, 2026 (week ending September 26).
  SHA-256: `e33ba5adcf0eb213d1d60ee96b55b9486d9a871250a99e28d6258a424cc0294e`
- `frb_h15_10022026.html`: https://www.federalreserve.gov/releases/h15/, the
  H.15 Selected Interest Rates release dated October 2, 2026 (rates through
  October 1).
  SHA-256: `3db321370973409394e859b257abcff321ce09c9fa7eecb6a90c7d315289d554`
