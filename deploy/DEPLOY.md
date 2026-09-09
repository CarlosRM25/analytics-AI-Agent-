# Deploying the public demo — start to finish

A first-time walkthrough of putting this agent on Cloud Run and wiring it to the
portfolio site. `MONITORING.md` covers what to set up *around* the deploy (alerts,
caps, kill switch); this covers the deploy itself.

Budget about 90 minutes the first time. Nothing here costs money at demo volume —
see [What this actually costs](#what-this-actually-costs).

---

## 0. The mental model

Three separate accounts, doing three different jobs:

| Service | What it holds | Cost |
| --- | --- | --- |
| **Google Cloud Run** | The container — Flask + the agent + the baked-in SQLite snapshot | Free tier covers this |
| **Upstash** | Redis: rate-limit counters, response cache, monthly budget counter | Free tier |
| **Anthropic** | The API key the agent calls Claude with | The only real cost, capped twice |

Plus **Vercel**, which already hosts the portfolio and just needs to be told the
Cloud Run URL.

**Redis is not a Google product here.** Cloud Run reaches Upstash over the public
internet using a TLS `rediss://` URL. There is nothing to create in the GCP console
for it, and you do not need a VPC connector.

> **Do not subscribe to "Redis Cloud Cache and Vector Database" in GCP Marketplace.**
> That is Redis Inc's commercial product billed through your GCP account — a 14-day
> trial that converts to a paid subscription. It is not what this project needs.
>
> Google's own Redis (**Memorystore**) is also the wrong fit: the smallest instance
> runs tens of dollars a month, and Cloud Run can only reach it through a Serverless
> VPC Access connector — more cost, more moving parts.

---

## 1. Upstash — Redis (~5 minutes)

Without a working `REDIS_URL` the app refuses to boot in `DEPLOY_MODE=deployed`
(see `_REQUIRED` in `config.py`). It is what makes the demo safe to put in front of
strangers: the rate limiter, the response cache and the budget cutoff all live in it.

1. Go to <https://console.upstash.com> and sign up (GitHub login is fine).
2. **Create Database** → type **Redis**.
   - **Name:** `analytics-agent`
   - **Region:** the one nearest `us-west1` (Oregon / N. California). Same-region
     matters less than you'd think — the agent makes a handful of Redis calls per
     question against ~15 seconds of Claude API time.
   - **Type:** Regional. Global costs more and buys nothing here.
3. Open the database → **Details** → copy the connection string starting with
   **`rediss://`** — two s's, the TLS one. Not `redis://`, not the REST URL, not the
   `UPSTASH_REDIS_REST_TOKEN`.

   It looks like `rediss://default:AbC123...@us1-example-12345.upstash.io:6379`.

4. Keep it handy for the next few minutes. It is a password — it goes into Secret
   Manager in step 4, never into git.

**Free tier:** generous enough that this demo cannot realistically exceed it — the
app caps itself at 300 questions/day globally and 5 per visitor. Check the current
limits on their pricing page; they have changed over time, so the figure quoted in
`MONITORING.md` may be stale.

*Alternative if Upstash gives you trouble:* a free database direct from
<https://redis.io> (Redis Cloud's free tier — sign up on their own site, **not**
through GCP Marketplace) also gives you a `rediss://` URL and works identically.

---

## 2. Install the gcloud CLI (~10 minutes)

You can do most of GCP in the browser, but the deploy and the secrets are far easier
from the command line, and it's the part you'd lift into CI later.

1. Download the Windows installer: <https://cloud.google.com/sdk/docs/install>
2. Run it. Accept the defaults, and **leave "Run gcloud init" checked** at the end.
3. `gcloud init` opens a browser to log in. Use the Google account the Cloud account
   is on.
4. **Close and reopen your terminal** — the installer edits PATH and existing shells
   won't see it.
5. Confirm:

```bash
gcloud version
```

If `gcloud` still isn't found, it installed to
`C:\Users\carlo\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin` — add that to
PATH, or call the full path.

---

## 3. Set up the GCP project (~15 minutes)

### 3a. Project

"My First Project" works, but a named project is easier to find later and easier to
delete if you want to start over.

```bash
gcloud projects create analytics-agent-demo --name="Analytics Agent Demo"
```

```bash
gcloud config set project analytics-agent-demo
```

If the ID is taken, add digits: `analytics-agent-demo-2026`. Project IDs are globally
unique and **permanent** — you cannot rename one.

To use the existing project instead:

```bash
gcloud projects list
```

then `gcloud config set project <the PROJECT_ID column>`.

### 3b. Billing

Cloud Run will not deploy without a billing account attached, even though you'll stay
inside the free tier. A new account comes with $300 of credit for 90 days.

Console → **Billing** → confirm you have a billing account, then link it:

```bash
gcloud billing accounts list
```

```bash
gcloud billing projects link analytics-agent-demo --billing-account=ACCOUNT_ID
```

### 3c. Enable the APIs

Each is off by default, and the deploy fails with a permission-shaped error if one is
missing.

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
```

Takes a minute or two.

### 3d. Set your default region

`us-west1` is Oregon — closest region to Seattle, and what `cloudrun.yaml` assumes.

```bash
gcloud config set run/region us-west1
```

---

## 4. Put the secrets in Secret Manager (~5 minutes)

Two secrets. Neither goes in the image, the repo, or a `--set-env-vars` flag — env
vars are visible in the console and in `gcloud run services describe`.

```bash
gcloud secrets create anthropic-api-key --replication-policy=automatic
```

```bash
gcloud secrets create redis-url --replication-policy=automatic
```

Now the values. **Don't paste secrets as command arguments** — they land in your
shell history. Pipe them from a file, then delete it. The API key is already in
`.env`, so read it from there:

```bash
grep '^ANTHROPIC_API_KEY=' .env | cut -d= -f2- | tr -d '\r\n' > /tmp/k && gcloud secrets versions add anthropic-api-key --data-file=/tmp/k && rm /tmp/k
```

For the Upstash URL, this reads it without echoing to the screen:

```bash
read -rs -p "Paste the rediss:// URL: " R && printf %s "$R" > /tmp/r && unset R && gcloud secrets versions add redis-url --data-file=/tmp/r && rm /tmp/r
```

Verify both landed without a trailing newline — a stray `\n` on an API key is a
classic silent 401:

```bash
gcloud secrets versions access latest --secret=anthropic-api-key | wc -c
```

Expect **108**. Then check the Redis one is a plausible length (not 0, not 1):

```bash
gcloud secrets versions access latest --secret=redis-url | wc -c
```

### Let Cloud Run read them

Cloud Run runs as the Compute Engine default service account, which cannot read
secrets until you grant it.

```bash
PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)') && for s in anthropic-api-key redis-url; do gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"; done
```

---

## 5. Build and push the image (~15 minutes)

> **Why not `gcloud run deploy --source .`?** That hands the repo to Cloud Build,
> which looks for a `Dockerfile` at the repo **root**. Ours is at `deploy/Dockerfile`,
> so the source path falls back to buildpacks and produces a broken image. Building
> locally is also easier to debug — you can run the exact container before shipping it.

### 5a. Build and test locally first

Make sure Docker Desktop is running (whale icon in the tray).

```bash
docker build -f deploy/Dockerfile -t analytics-agent .
```

The build asserts `analytics.db` and the model artifact are present, so missing data
fails the build rather than producing a container that 500s at runtime.

Smoke-test before pushing:

```bash
docker run --rm -p 8080:8080 -e DEPLOY_MODE=local -e ANTHROPIC_API_KEY=dummy analytics-agent
```

In another terminal:

```bash
curl localhost:8080/health
```

`DEPLOY_MODE=local` so it boots without Redis — you're testing that the image is
sound, not the demo middleware. Ctrl-C when `/health` comes back OK.

### 5b. Create an Artifact Registry repo

One-time. This is where the image lives.

```bash
gcloud artifacts repositories create web --repository-format=docker --location=us-west1 --description="Container images"
```

### 5c. Authenticate Docker to it

One-time. Without this the push fails with a 403.

```bash
gcloud auth configure-docker us-west1-docker.pkg.dev
```

### 5d. Tag and push

```bash
IMAGE="us-west1-docker.pkg.dev/$(gcloud config get-value project)/web/analytics-agent:latest" && docker tag analytics-agent "$IMAGE" && docker push "$IMAGE" && echo "$IMAGE"
```

~188 MB, so a few minutes on a home connection. Note that `IMAGE` value — the next
step uses it, and it must be set in the *same* terminal.

---

## 6. Deploy (~5 minutes)

```bash
gcloud run deploy analytics-agent --image "$IMAGE" --region us-west1 --allow-unauthenticated --min-instances 0 --max-instances 3 --memory 512Mi --cpu 1 --concurrency 4 --timeout 120 --set-env-vars "DEPLOY_MODE=deployed,ANALYST_MODEL=claude-haiku-4-5,MAX_AGENT_ITERS=6,DEMO_ENABLED=true,RATE_LIMIT_PER_VISITOR_PER_DAY=5,GLOBAL_DAILY_QUESTION_CAP=300,MONTHLY_BUDGET_USD=15,CORS_ALLOWED_ORIGIN=https://portfolio-liart-rho-94.vercel.app" --set-secrets "ANTHROPIC_API_KEY=anthropic-api-key:latest,REDIS_URL=redis-url:latest"
```

What the flags buy you:

- `--allow-unauthenticated` — it's a public demo. Without it every request needs a
  Google identity token.
- `--min-instances 0` — scales to zero when nobody's looking, which is why it's free.
  Costs a ~5–10s cold start on the first question after an idle period.
- `--max-instances 3` — a backstop. The app's own daily cap is the real limit.
- `--concurrency 4` — the agent loop is mostly waiting on the Claude API, so one
  container can handle several questions at once.
- `--timeout 120` — matches the gunicorn timeout in the Dockerfile. A hard question
  with a couple of SQL retries can take 30s.

It prints a **Service URL** like `https://analytics-agent-abc123-uw.a.run.app`.

```bash
SERVICE_URL=$(gcloud run services describe analytics-agent --region us-west1 --format='value(status.url)') && echo "$SERVICE_URL" && curl "$SERVICE_URL/health"
```

To prove the whole loop works end to end — schema read, SQL written and run, answer
composed:

```bash
curl -X POST "$SERVICE_URL/ask" -H 'Content-Type: application/json' -d '{"question":"Which neighborhood has the most benchmarked buildings?"}'
```

That exercises the agent, but it does **not** test CORS. `_cors` in `app/api.py` sets
`Access-Control-Allow-Origin` to the configured value on every response without
looking at the request's `Origin` — the *browser* is what compares the two and blocks
the mismatch. curl ignores CORS entirely, so it will succeed no matter what the
header says. To check the header is the value you expect:

```bash
curl -sS -D - -o /dev/null "$SERVICE_URL/health" | grep -i access-control-allow-origin
```

The real CORS test is loading the deployed portfolio page and asking a question.

---

## 7. Wire it to the portfolio (~10 minutes)

Two values on two services. They must agree or the browser blocks the call.

### 7a. Cloud Run already knows the portfolio

`CORS_ALLOWED_ORIGIN` went in at step 6. The backend advertises **exactly one origin**
— `_cors` in `app/api.py` echoes that one configured value, never a wildcard and never
the caller's own origin. Consequences:

- Vercel **preview** deploys get their own origin and will fail CORS. The offline
  gallery still works there. Expected — don't chase it.
- `localhost:4321` fails too. To develop against the live backend, temporarily point
  `CORS_ALLOWED_ORIGIN` at localhost.
- **If you ever add a custom domain, update this and redeploy the service**, or the
  demo goes dead on the new domain.

One thing to be clear-eyed about: CORS is a **browser** mechanism. It stops another
website from spending your API budget through a visitor's browser, but it does not
stop anyone who runs `curl` against the service URL. What actually bounds the spend is
the per-visitor rate limit, the global daily cap, and the monthly budget cutoff — all
enforced server-side in `app/`, all independent of where the request came from.

### 7b. Tell Vercel the Cloud Run URL

1. <https://vercel.com> → project **portfolio** → **Settings → Environment Variables**
2. Add:
   - **Key:** `PUBLIC_AGENT_API_URL`
   - **Value:** the Service URL, no trailing slash
   - **Environments:** Production
3. **Deployments → ⋯ → Redeploy.**

The redeploy is not optional. `PUBLIC_`-prefixed vars are inlined into the client
bundle by Vite at **build time**, so saving the variable changes nothing until the
site is rebuilt. That's also why the URL isn't a secret — it ships in the JS. It's
protected by CORS, the rate limiter and the daily cap, not by being hidden.

### 7c. Flip the card to live

In the portfolio repo, `src/data/projects.ts`:

```ts
status: "live",   // was "building"
```

`/demo` switches from the "not live yet" panel to a real input box on its own —
`AskPanel.astro` branches on `PUBLIC_AGENT_API_URL` at build time — but the badge on
the project card is driven by `status`.

---

## 8. Guardrails — do these before you share the link

Details in `MONITORING.md`. The two that actually stop a bill:

1. **Anthropic Console → Settings → Limits** → a monthly spend limit on the workspace
   holding this key. Set it *above* `MONTHLY_BUDGET_USD` (e.g. $20 vs $15) so the
   app's own cutoff trips first and the hard cap stays a backstop.
2. **GCP → Billing → Budgets & alerts** → a ~$5 budget on the project, email alerts at
   50/90/100%. Cloud Run should stay near zero; this catches the unexpected.

Three layers already live in the code: per-visitor rate limit (5/day), global daily
cap (300), and a monthly dollar budget that disables the demo when hit. Plus a manual
kill switch:

```bash
gcloud run services update analytics-agent --region us-west1 --update-env-vars DEMO_ENABLED=false
```

`/ask` starts returning `{"disabled": true}` immediately and the frontend falls back
to the gallery. No redeploy, no downtime.

---

## 9. Warm the cache

```bash
python -m deploy.warm_cache "$SERVICE_URL"
```

Runs `evals/questions.yaml` through the live `/ask` once so the common questions are
already in the response cache. The first recruiter to try an obvious question gets an
instant, free answer instead of a cold start plus 15 seconds.

Costs a few cents, once.

---

## What this actually costs

- **Cloud Run** — the free tier covers roughly two million requests and a large
  monthly allowance of vCPU-seconds. A demo answering a few hundred questions is far
  inside it, and scaling to zero means no idle charge.
- **Upstash** — free tier, not close to the limit.
- **Artifact Registry** — a 188 MB image is pennies a month; the free allowance covers
  it.
- **Anthropic API** — the only line item that grows. ~$0.004 per cached-prompt question
  on Haiku 4.5, hard-capped at `MONTHLY_BUDGET_USD=15`.

Realistic steady state: **$0 from Google, single-digit dollars from Anthropic in a busy
month.**

The one way to get surprised is leaving `--min-instances` above 0, which bills for an
always-warm container. Keep it at 0.

---

## Troubleshooting

**`PERMISSION_DENIED` on deploy** — an API isn't enabled (3c) or billing isn't linked
(3b).

**Revision failed to start / "container failed to listen on $PORT"** — read the logs:

```bash
gcloud run services logs read analytics-agent --region us-west1 --limit 50
```

Usually pydantic rejecting the environment: `missing required settings for
DEPLOY_MODE=deployed`. That means `ANTHROPIC_API_KEY`, `REDIS_URL` or
`CORS_ALLOWED_ORIGIN` didn't arrive. Check what the service actually has:

```bash
gcloud run services describe analytics-agent --region us-west1 --format='value(spec.template.spec.containers[0].env)'
```

**Secret access denied in the logs** — the IAM binding in step 4 didn't apply, or you
re-created the secret afterwards. Re-run that command.

**`/health` works but `/ask` returns 500** — almost always Redis. Deployed mode boots
with a syntactically valid `REDIS_URL` but only *connects* on the first `/ask`. Check
it's the `rediss://` URL and the password wasn't truncated.

**Browser console says CORS blocked** — the origin on Cloud Run doesn't exactly match
the site. It's an exact string compare: scheme, host, no trailing slash.
`https://portfolio-liart-rho-94.vercel.app` — not `http://`, not `.../`.

**The demo answers nothing and the network tab shows `{"disabled": true}`** — either
`DEMO_ENABLED=false` or the monthly budget tripped. Check the `budget:<YYYY-MM>` key
in the Upstash console.

**Everything works but the site still shows "Not live yet"** — you saved the Vercel env
var but didn't redeploy. See 7b.

---

## Redeploying later

After a code change:

```bash
docker build -f deploy/Dockerfile -t analytics-agent . && docker tag analytics-agent "$IMAGE" && docker push "$IMAGE" && gcloud run deploy analytics-agent --image "$IMAGE" --region us-west1
```

Env vars and secrets persist across deploys — you only pass them again when they change.

After the annual Seattle data refresh, rebuild the baked-in snapshot first:

```bash
python -m ingest.run --source seattle_energy --full-refresh && python -m model.seattle_energy.train
```

then commit the regenerated `analytics.db` and model artifact, and rebuild as above.
