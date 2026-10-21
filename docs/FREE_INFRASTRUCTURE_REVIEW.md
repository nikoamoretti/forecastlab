# Free infrastructure review

Checked 2026-09-05. Research depth, model settings, search allowances and the three-estimate method are unchanged. No account, billing or production connection changes were made.

## Recommendation

Evaluate Neon Free first. It preserves Postgres and has the smallest migration surface. The public plan currently includes 100 CU-hours per project per month and 0.5 GB storage. ForecastLab's live database measured 37,765,120 bytes (about 38 MB). A scenario of 96 daily wakeups, each lasting five minutes at 0.25 CU, uses about 60 CU-hours in a 30-day month, before query time and other traffic. This fits the published compute quota in that scenario; actual compute size and usage still require verification. The current Vercel-managed Launch resource's downgrade or transfer eligibility is unverified. [Neon pricing](https://neon.com/pricing)

The limiting difference is recovery: Neon Free offers up to six hours or 1 GB of change history, not seven days of native point-in-time recovery. Existing application backups retain at least seven completed daily restore points in private Blob. Those can provide daily recovery, but cannot recover every intermediate transaction. The original native seven-day recovery requirement therefore remains unmet on Free unless the owner explicitly accepts daily snapshots instead. Blob and the existing Vercel subscription may still cost money; this is not a zero-cost total-hosting claim. [Neon pricing](https://neon.com/pricing)

## Alternatives

| Option | Published free provision | Operational fit |
| --- | --- | --- |
| Supabase Free | 500 MB Postgres, two active free projects; no automatic backups or PITR | Next-best Postgres candidate. Requires account capacity, compatible Postgres version, pooler setup, data import and restore verification. Low-activity projects can pause; routine application activity is not an availability guarantee. |
| Cloudflare D1 Free | 500 MB per database, 5 GB account total; 5M rows read and 100k written/day; seven-day Time Travel | Strong free recovery allowance, but uses SQLite semantics and different APIs. ForecastLab's cloud row locks, job leases, ledger transactions and migrations require redesign and concurrency tests. Not a connection-string replacement. |
| Turso Free | 5 GB, 500M rows read and 10M written/month; one-day PITR | Capacity is ample, but current SQLAlchemy/Postgres assumptions and restore operations need a compatibility project. One-day native recovery falls short of the current seven-day requirement. |

Sources: [Supabase pricing](https://supabase.com/pricing), [Supabase pausing](https://supabase.com/docs/guides/platform/free-project-pausing), [Supabase backups](https://supabase.com/docs/guides/platform/backups), [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/), [D1 limits](https://developers.cloudflare.com/d1/platform/limits/), [Turso pricing](https://turso.tech/pricing?frequency=monthly).

Self-hosted Postgres or SQLite has no database license fee, but still needs an always-on machine, storage, monitoring and backups. The Mac being off rules out relying on its local worker. No suitable already-paid remote machine has been verified for this task.

## Migration preparation

1. In the authenticated dashboard, verify the actual plan, compute bounds, suspension, usage and whether the current resource can move to Free. Do not change other projects' organization billing.
2. Resolve the recovery-policy choice before any downgrade. Keep the existing research policy and frozen qualification results intact.
3. If a new Postgres project is needed, create an isolated target with an appropriate supported server version and a direct migration connection. Freeze dispatch, drain work, and snapshot the source.
4. Use the existing migration and import/restore tools; verify all row hashes, IDs, contracts, ledgers, exports and foreign keys. Rehearse restoration on a separate target.
5. Stage production API connections to the target with spending disabled. Verify authentication, duplicate launches, reservations and lease recovery, private artifact readback and summaries. Keep the old database for rollback until cutover checks pass; do not delete it during migration.

## Authentication

The application already implements GitHub OAuth; the remaining account registration is a setup issue rather than a research or database dependency. Retain it for the smallest change. Replacing it with passkeys or another managed login could remove that registration dependency, but requires a new enrollment, recovery and security design. It would not remove the need for the owner to authenticate during setup.
