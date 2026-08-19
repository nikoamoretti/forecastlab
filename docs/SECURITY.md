# Security

- No authentication. Bind to localhost.
- Secrets are stored in `data/local/credentials.json` with mode `0600`.
- Settings GET never returns API keys, only booleans.
- Logs run through a JSON formatter that redacts bearer tokens and `api_key=` patterns.
- Fetch allows only http/https, blocks private/link-local/metadata hosts, and caps redirects, bytes, and time.
- Local fixture hosts are allowed only when `allow_local_fixtures` is on.
- There is no arbitrary code execution and no tool that writes outside the data directory by design.
