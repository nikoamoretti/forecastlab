# Security

- No authentication. Bind to localhost.
- Secrets are stored in `data/local/credentials.json` with mode `0600`.
- Settings GET never returns API keys, only booleans.
- Logs run through a JSON formatter that redacts bearer tokens and `api_key=` patterns.
- All evidence, Wayback, and external watcher fetches use one safe HTTP client: no automatic unrestricted redirects, each hop revalidated, streamed byte cap, declared Content-Length rejected when oversized, content-type allowlist, and no embedded credentials.
- Trusted-domain classification uses exact hostname boundaries (`host == domain or host.endswith("." + domain)`). `bls.gov.attacker.example` is not a primary source.
- Execution contexts, exports, and benchmark records never store API keys.
- Local fixture hosts are allowed only when `allow_local_fixtures` is on.
- There is no arbitrary code execution and no tool that writes outside the data directory by design.
