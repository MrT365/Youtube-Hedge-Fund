# Deferred Items — Phase 0 Foundation

## From plan 00-06 (CLI skeleton + doctor)

### Pre-existing test failures (out-of-scope for 00-06)

`tests/unit/test_migrations.py` has 4 failing tests that pre-date this plan
(verified by stashing 00-06's changes and running on commit `c2ce9df`'s parent
state). The failures appear to be a fixture/isolation problem: alembic upgrade
runs but the resulting tables are not visible to the assertion connection,
which suggests either a CWD issue or a sqlite memory-vs-file mode mismatch
in the test setup.

Failing tests:
- `test_alembic_upgrade_head_creates_tables`
- `test_runs_status_check_constraint`
- `test_heartbeat_singleton_row`
- `test_upgrade_idempotent`

**Action required:** investigate as part of plan 00-03 follow-up or a fresh
remediation plan. Not blocking 00-06 because these were already broken before
00-06 started and are unrelated to CLI/doctor changes.

Discovered: 2026-05-04 (during 00-06 execution).
