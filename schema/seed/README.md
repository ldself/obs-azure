# schema/seed/

Seed data scripts for local development, applied in filename order by
`make db-seed` against the local DuckDB database (Local Dev Environment Spec
v1.1 §8.2). Name files with a numeric prefix to control ordering, e.g.
`010_users.sql`, `020_business_rules.sql`.

Phase 0 ships no seed data — all `obs.*` tables are created empty. Seed scripts
are added by the phases that need usable local fixtures (e.g. business-rule
rates, a local admin user).
