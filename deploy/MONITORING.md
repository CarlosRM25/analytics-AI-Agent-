# Public-demo hardening — the parts that live outside the repo

The code side of M8 (rate limiter, response cache, budget cutoff, CORS, kill
switch) ships in `app/`. These are the account-level pieces to set up once, in
order. Everything here is free-tier.

## 1. Redis — Upstash (rate limit + cache + budget counter)

1. Create a database at <https://console.upstash.com> (free tier: 10k commands/day,
   256 MB — far more than this demo needs).
2. Copy the **`rediss://`** connection URL (TLS one, not `redis://`).
3. Local: put it in `.env` as `REDIS_URL=rediss://...`.
   Deployed: `gcloud secrets versions add redis-url --data-file=-` (see
   `cloudrun.yaml`), which Cloud Run injects as `REDIS_URL`.

Without a valid `REDIS_URL` the whole demo layer is inert: the cache always
misses, the rate limiter always allows, the budget never trips. `DEPLOY_MODE=deployed`
refuses to boot without it.

## 2. Anthropic Console — hard monthly cap (backstop for control #5)

Console → **Settings → Limits** → set a monthly **spend limit** on the workspace
that holds this API key (e.g. $20 — above `MONTHLY_BUDGET_USD` so the app's own
cutoff trips first). This is the "the app counter missed" safety net.

## 3. GCP — billing budget + alerts

Billing → **Budgets & alerts** → create a budget (~$5) scoped to the project,
with email alerts at 50 / 90 / 100%. Cloud Run itself stays near $0 at demo
volume; this is for peace of mind, and it catches anything unexpected (egress,
a runaway revision).

## 4. UptimeRobot — liveness

Add an HTTP(s) monitor on `https://<service-url>/health`, 5-minute interval,
alert contact = your email. `GET /health` is unauthenticated and does no work.

## 5. Cloud Run — error-rate alert (optional)

Logging → **Log-based alerts** → alert when `severity>=ERROR` from the
`analytics-agent` service exceeds ~5 in 5 min → email. Catches a broken deploy or
an upstream (Anthropic / Upstash) outage.

## 6. Kill switch

- Manual: `gcloud run services update analytics-agent --update-env-vars DEMO_ENABLED=false`
  (or flip it in the console). `/ask` returns `{"disabled": true}` immediately;
  the frontend falls back to the example gallery. No redeploy.
- Automatic: when the month's summed `usage.cost_usd` reaches `MONTHLY_BUDGET_USD`,
  the app sets that behaviour itself until the UTC month rolls over.

## 7. After every deploy

```bash
python -m deploy.warm_cache https://<service-url>
```

Runs `evals/questions.yaml` through the live `/ask` once so the common questions
are cached (instant + free) from the first real visitor.
