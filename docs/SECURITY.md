# Security

- No authentication. Bind to localhost.
- Secrets are stored in `data/local/credentials.json` with mode `0600`.
- Settings GET never returns API keys, only booleans.
- Logs run through a JSON formatter that redacts bearer tokens and `api_key=` patterns.
- All evidence, Wayback, and external watcher fetches use one safe HTTP client: HTTP/HTTPS only, no embedded credentials, DNS and IP validation, redirect-target validation, redirect count, timeouts, content-type allowlist, and rejection of private, loopback, link-local, multicast, and metadata addresses. Bodies are read with `client.stream("GET", ...)` and stopped as soon as the running byte total exceeds the ceiling. `client.get()` is not used for external evidence or watcher bodies. Declared Content-Length above the ceiling is rejected before any body is kept.
- User-created JSON and HTML watches are validated at creation. They cannot use localhost, loopback, private IPv4/IPv6, metadata hosts, fixture hosts, or an internal source type. External watches never pass `allow_local_fixtures=True`. The demo indicator is an internal source: the worker reads it in-process with no network request, and users cannot submit that source type.
- Trusted-domain classification uses exact hostname boundaries (`host == domain or host.endswith("." + domain)`). `bls.gov.attacker.example` is not a primary source.
- Execution contexts, exports, and benchmark records never store API keys.
- Local fixture hosts are allowed only when `allow_local_fixtures` is on.
- There is no arbitrary code execution and no tool that writes outside the data directory by design.
