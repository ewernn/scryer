## 2026-05-02 — Deferred: cold-tier archive (DECISION LOCKED)

Scoped a "Run hot data → R2 cold tier" architecture (Parquet manifests, monthly
audit_events DETACH+archive, Cron promotion job, dual-tier read path with
fallback, thaw_run reversibility). 8 parallel investigators + 1 critic.

**Verdict: defer indefinitely.** Critic gave 9/10 STRONG arguments. Cost agent
verified pricing (Neon $0.35/GB-mo + $5/mo min, R2 $0.015/GB-mo).

| Scale | Runs/day | Yr-3 PG | Untiered $/yr | Tiered $/yr | Saved | ROI |
|-------|----------|---------|---------------|-------------|-------|-----|
| Beta | 50 | 5 GB | $60 (min) | $60 (min) | $0 | never |
| Growing | 500 | 51 GB | $214 | $29 | $185 | 5.7 yr |
| Scale-out | 5,000 | 510 GB | $2,142 | $260 | $1,882 | 20 mo |

**Build trigger (ANY one):**
1. PG cumulative storage > 50 GB, OR
2. Neon storage line-item (not minimum) > $20/mo (~57 GB used), OR
3. User requests > 2 yr audit-log retention for compliance.

Until then: pay $5-30/mo, ship product. Monitor disk via existing §15 #20.

**Side effects surfaced (real bugs, separate tickets):**
- web.py:151 dumps up to 1000 results inline, no pagination → Wave 1d
- ix_results_run_id_score_value indexes wrong column for ORDER BY → Wave 1e

Agent outputs archived at: `/private/tmp/claude-501/-Users-ewern-Desktop-code-trait-stuff-traitinterp/5b877adf-1d6e-4945-b429-6ff3d008e519/tasks/`

---

