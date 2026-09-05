# Original BLS releases republished by the Department of Labor

Public U.S. government documents, fetched September 5, 2026. The original bytes
exercise PDF extraction, explicit observation periods, publication timestamps,
headline units, and next-release announcements. These are historical test
fixtures and are never used as live source cache entries.

- https://www.dol.gov/newsroom/economicdata/empsit_09042026.pdf
  SHA-256: `3307e36fa600e4f9ade4eb61d60b34814d6547d3e5c7533cda7221babb7ae10c`
- https://www.dol.gov/newsroom/economicdata/cpi_08122026.pdf
  SHA-256: `6b22335813df900e7c379e8db8d2459463744d6530f28626dfd377a314c0c0ab`

The documents announce the September CPI and October employment releases.
Their announced times were checked against the current New York Fed economic
calendars in an isolated Vercel preview. Calendar test HTML contains only the
relevant factual entries; the production parser was also tested on the original
retrieved calendar pages.
