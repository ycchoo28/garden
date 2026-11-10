# Architecture rules have an explicit enforcement tier

ADR 0026 defines ownership; discovered package boundaries and declared reporting reads now make its mechanically decidable parts executable.
Prefer construction, then static checks, behavior tests, review and finally prose, with one primary enforcement mechanism per rule.
Static checks run without Postgres in `just quick`; command-level permitted and prohibited fixtures prove their diagnostics in the Scaffold job.
Exceptions live beside the checks with reasons, rather than in inventories of current modules.
Business ownership, the meaning of consumed data and public-interface quality remain review decisions, using the required [ownership checklist](../architecture-review.md).
See the [enforcement map](../architecture-review.md#enforcement-map) for the check responsible for each rule.
