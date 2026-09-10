# Deploying the public demo — start to finish

A first-time walkthrough of putting this agent on Cloud Run and wiring it to the
portfolio site. `MONITORING.md` covers what to set up *around* the deploy (alerts,
caps, kill switch); this covers the deploy itself.

Budget about 90 minutes the first time. Nothing here costs money at demo volume —
see [What this actually costs](#what-this-actually-costs).

> **Shell:** every command here is **PowerShell 5.1 on Windows**, because that's
> what this project is developed on. Three things differ from the bash you'll find
> in most GCP docs, and all three fail *loudly* except the last one, which fails
> silently and corrupts a secret:
>
> | Bash | PowerShell | Why |
> | --- | --- | --- |
> | `curl` | **`curl.exe`** | `curl` is an alias for `Invoke-WebRequest`, which takes completely different flags. The `.exe` runs the real binary in `C:\Windows\System32`. |
> | `a && b` | `a; b` | `&&` is a parser error in PS 5.1. Use `a; if ($?) { b }` when b must depend on a. |
> | `--format='value(x)'` | `--format="value(x)"` | Unquoted parens make PowerShell try to *execute* `x` as a command. |
> | `printf %s "$v" > f` | `[IO.File]::WriteAllText(f, $v)` | `Out-File` adds a UTF-8 BOM **and** a trailing CRLF; `Set-Content` adds the CRLF. Either one corrupts an API key with no visible symptom. |
> | `curl -d '{"a":"b c"}'` | `Invoke-RestMethod -Body (… \| ConvertTo-Json)` | PowerShell mangles inline JSON on its way to a native exe — quotes stripped, or split on spaces. See step 6. |
>
> On macOS or Linux, drop the `.exe`, swap `;` for `&&`, and use `$(...)` for
> command substitution.

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
3. Open the database and find the **connection string**, not the REST credentials.
   In the connect panel, switch off the **REST** tab to the `redis-cli` / Python one.

   It looks like `rediss://default:AbC123...@us1-example-12345.upstash.io:6379`.

   - If the password shows as `********`, click the reveal/eye icon **before**
     copying, or you'll copy literal asterisks.
   - If the scheme shows `redis://` rather than `rediss://`, that's fine — try it
     as-is and see step 1a. There is no separate "rediss link" to hunt for.
   - `UPSTASH_REDIS_REST_TOKEN` and the `https://` URL are the HTTP API. Wrong ones.

4. Keep it handy. It is a password — it goes into Secret Manager in step 4, never
   into git.

### 1a. Check it before you go further

A bad `REDIS_URL` doesn't surface until the first `/ask` in production, so prove it
now. From the repo root:

```powershell
.\.venv\Scripts\python.exe -c "import redis,sys; print('PING ->', redis.from_url(sys.argv[1], socket_connect_timeout=10).ping())" "PASTE_THE_URL_HERE"
```

`PING -> True` means you have the right string — use exactly that as `REDIS_URL`.

- `ValueError` about schemes → you pasted the `https://` REST URL, or the host alone.
- `getaddrinfo failed` → hostname wrong or mistyped.
- `AuthenticationError` → password wrong, or you copied it while masked.
- An SSL/handshake error on `rediss://` → retry with `redis://` (TLS is off on that
  database), and vice versa.

Since this puts the password in your shell history, clear it once you're done:

```powershell
Clear-History; Remove-Item (Get-PSReadlineOption).HistorySavePath -ErrorAction SilentlyContinue
```

**Free tier:** generous enough that this demo cannot realistically exceed it — the
app caps itself at 300 questions/day globally and 5 per visitor. Check the current
limits on their pricing page; they have changed over time, so the figure quoted in
`MONITORING.md` may be stale.

*Alternative if Upstash gives you trouble:* a free database direct from
<https://redis.io> (Redis Cloud's free tier — sign up on their own site, **not**
through GCP Marketplace) also gives you a connection string and works identically.

---

## 2. Install the gcloud CLI (~10 minutes)

You can do most of GCP in the browser, but the deploy and the secrets are far easier
from the command line, and it's the part you'd lift into CI later.

1. Download the Windows installer: <https://cloud.google.com/sdk/docs/install>
2. Run it. Accept the defaults, and **leave "Run gcloud init" checked** at the end.
3. `gcloud init` opens a browser to log in. Use the Google account the Cloud account
   is on.
4. **Close and reopen PowerShell** — the installer edits PATH and existing shells
   won't see it.
5. Confirm:

```powershell
gcloud version
```

If `gcloud` still isn't found, it installed to
`C:\Users\carlo\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin`. Add that to
PATH, or for this session only:

```powershell
$env:Path += ";C:\Users\carlo\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin"
```

---

## 3. Set up the GCP project (~15 minutes)

### 3a. Project

"My First Project" works, but a named project is easier to find later and easier to
delete if you want to start over.

```powershell
gcloud projects create analytics-agent-demo --name="Analytics Agent Demo"
```

```powershell
gcloud config set project analytics-agent-demo
```

If the ID is taken, add digits: `analytics-agent-demo-2026`. Project IDs are globally
unique and **permanent** — you cannot rename one.

To use the existing project instead, list them and set the one you want:

```powershell
gcloud projects list
```

```powershell
gcloud config set project PASTE_THE_PROJECT_ID
```

### 3b. Billing

Cloud Run will not deploy without a billing account attached, even though you'll stay
inside the free tier. A new account comes with $300 of credit for 90 days.

Console → **Billing** → confirm you have a billing account, then link it:

```powershell
gcloud billing accounts list
```

```powershell
gcloud billing projects link analytics-agent-demo --billing-account=PASTE_ACCOUNT_ID
```

### 3c. Enable the APIs

Each is off by default, and the deploy fails with a permission-shaped error if one is
missing.

```powershell
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
```

Takes a minute or two.

### 3d. Set your default region

`us-west1` is Oregon — closest region to Seattle, and what `cloudrun.yaml` assumes.

```powershell
gcloud config set run/region us-west1
```

---

## 4. Put the secrets in Secret Manager (~5 minutes)

Two secrets. Neither goes in the image, the repo, or a `--set-env-vars` flag — env
vars are visible in the console and in `gcloud run services describe`.

```powershell
gcloud secrets create anthropic-api-key --replication-policy=automatic
```

```powershell
gcloud secrets create redis-url --replication-policy=automatic
```

### 4a. The API key

It's already in `.env`, so read it from there rather than pasting it. Run this from
the repo root:

```powershell
$key = ((Select-String -Path .env -Pattern '^ANTHROPIC_API_KEY=' | Select-Object -First 1).Line -replace '^ANTHROPIC_API_KEY=','').Trim(); [System.IO.File]::WriteAllText("$env:TEMP\k", $key); "wrote $((Get-Item "$env:TEMP\k").Length) bytes"
```

Expect **108 bytes**. If you get 110 or 113 you used `Set-Content` or `Out-File`
somewhere — see the shell note at the top; those add a CRLF and a BOM, and Secret
Manager will store them, and the Anthropic API will 401 with no useful message.

```powershell
gcloud secrets versions add anthropic-api-key --data-file="$env:TEMP\k"; Remove-Item "$env:TEMP\k"; Remove-Variable key
```

### 4b. The Redis URL

Replace the placeholder with the string you verified in step 1a:

```powershell
[System.IO.File]::WriteAllText("$env:TEMP\r", "PASTE_THE_VERIFIED_REDIS_URL"); gcloud secrets versions add redis-url --data-file="$env:TEMP\r"; Remove-Item "$env:TEMP\r"
```

### 4c. Verify both round-trip cleanly

```powershell
foreach ($s in @("anthropic-api-key","redis-url")) { $v = gcloud secrets versions access latest --secret=$s; "{0,-18} {1} chars" -f $s, $v.Length }
```

The key should be **108**. The Redis URL should be somewhere in the 60–120 range —
what matters is that it isn't 0 or 1.

### 4d. Let Cloud Run read them

Cloud Run runs as the Compute Engine default service account, which cannot read
secrets until you grant it.

```powershell
$PN = gcloud projects describe (gcloud config get-value project) --format="value(projectNumber)"; foreach ($s in @("anthropic-api-key","redis-url")) { gcloud secrets add-iam-policy-binding $s --member="serviceAccount:$PN-compute@developer.gserviceaccount.com" --role="roles/secretmanager.secretAccessor" }
```

Note the quotes around `"value(projectNumber)"` — without them PowerShell tries to
run `projectNumber` as a command.

---

## 5. Build and push the image (~15 minutes)

> **Why not `gcloud run deploy --source .`?** That hands the repo to Cloud Build,
> which looks for a `Dockerfile` at the repo **root**. Ours is at `deploy/Dockerfile`,
> so the source path falls back to buildpacks and produces a broken image. Building
> locally is also easier to debug — you can run the exact container before shipping it.

### 5a. Build and test locally first

Make sure Docker Desktop is running (whale icon in the tray).

```powershell
docker build -f deploy/Dockerfile -t analytics-agent .
```

The build asserts `analytics.db` and the model artifact are present, so missing data
fails the build rather than producing a container that 500s at runtime.

Smoke-test before pushing:

```powershell
docker run --rm -p 8080:8080 -e DEPLOY_MODE=local -e ANTHROPIC_API_KEY=dummy analytics-agent
```

In a **second** PowerShell window:

```powershell
curl.exe localhost:8080/health
```

`DEPLOY_MODE=local` so it boots without Redis — you're testing that the image is
sound, not the demo middleware. Ctrl-C the first window when `/health` returns
`{"status":"ok"}`.

### 5b. Create an Artifact Registry repo

One-time. This is where the image lives.

```powershell
gcloud artifacts repositories create web --repository-format=docker --location=us-west1 --description="Container images"
```

### 5c. Authenticate Docker to it

One-time. Without this the push fails with a 403.

```powershell
gcloud auth configure-docker us-west1-docker.pkg.dev
```

### 5d. Tag and push

```powershell
$IMAGE = "us-west1-docker.pkg.dev/$(gcloud config get-value project)/web/analytics-agent:latest"; docker tag analytics-agent $IMAGE; docker push $IMAGE; $IMAGE
```

The image is **~870 MB** uncompressed (scipy, pandas, plotly and scikit-learn
are most of it). The push transfers compressed layers, so expect a few hundred
MB over the wire and **10-20 minutes on a home connection** the first time.
Later pushes only send layers that changed, which is usually just your code.

`$IMAGE` is a PowerShell variable and dies with the window. **Steps 5d and 6 must run
in the same PowerShell session.** If you closed it, just re-run the first assignment
before deploying.

---

## 6. Deploy (~5 minutes)

```powershell
gcloud run deploy analytics-agent --image $IMAGE --region us-west1 --allow-unauthenticated --min-instances 0 --max-instances 3 --memory 512Mi --cpu 1 --concurrency 4 --timeout 120 --set-env-vars "DEPLOY_MODE=deployed,ANALYST_MODEL=claude-haiku-4-5,MAX_AGENT_ITERS=6,DEMO_ENABLED=true,RATE_LIMIT_PER_VISITOR_PER_DAY=5,GLOBAL_DAILY_QUESTION_CAP=300,MONTHLY_BUDGET_USD=15,CORS_ALLOWED_ORIGIN=https://carlos-rubio-marroquin.com" --set-secrets "ANTHROPIC_API_KEY=anthropic-api-key:latest,REDIS_URL=redis-url:latest"
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

```powershell
$SERVICE_URL = gcloud run services describe analytics-agent --region us-west1 --format="value(status.url)"; $SERVICE_URL; curl.exe "$SERVICE_URL/health"
```

To prove the whole loop works end to end — schema read, SQL written and run, answer
composed. **Use `Invoke-RestMethod`, not curl, for this one:**

```powershell
Invoke-RestMethod -Uri "$SERVICE_URL/ask" -Method Post -ContentType "application/json" -Body (@{question="Which neighborhood has the most benchmarked buildings?"} | ConvertTo-Json)
```

> **Why not curl here.** Passing inline JSON to a native `.exe` from PowerShell 5.1
> is genuinely broken, and it fails in a way that looks like a server bug. Neither
> of the obvious forms survives:
>
> | Attempt | What curl actually receives |
> | --- | --- |
> | `-d '{"question":"x"}'` | `{question:x}` — quotes stripped |
> | `-d '{\"q\":\"two words\"}'` | split on the spaces into several arguments |
> | `-d $(… \| ConvertTo-Json)` | `{question:two words}` — quotes stripped |
>
> PowerShell doesn't re-quote an argument that already contains quote characters,
> so the exe's C runtime re-splits the mangled command line. `Invoke-RestMethod`
> has no native-exe boundary and no such problem.
>
> If you specifically need curl (to see response headers, say), put the body in a
> file and reference it with `@` — that survives intact:
>
> ```powershell
> [System.IO.File]::WriteAllText("$env:TEMP\q.json", '{"question":"Which neighborhood has the most benchmarked buildings?"}'); curl.exe -X POST "$SERVICE_URL/ask" -H "Content-Type: application/json" -d "@$env:TEMP\q.json"
> ```
>
> Plain GETs like `curl.exe "$SERVICE_URL/health"` are fine — the trap is only
> inline JSON.

That exercises the agent, but it does **not** test CORS. `_cors` in `app/api.py` sets
`Access-Control-Allow-Origin` to the configured value on every response without
looking at the request's `Origin` — the *browser* is what compares the two and blocks
the mismatch. curl ignores CORS entirely, so it will succeed no matter what the
header says. To check the header is the value you expect:

```powershell
(Invoke-WebRequest "$SERVICE_URL/health").Headers["Access-Control-Allow-Origin"]
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
stop anyone who runs curl against the service URL. What actually bounds the spend is
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

```powershell
gcloud run services update analytics-agent --region us-west1 --update-env-vars DEMO_ENABLED=false
```

`/ask` starts returning `{"disabled": true}` immediately and the frontend falls back
to the gallery. No redeploy, no downtime.

---

## 9. Warm the cache

```powershell
.\.venv\Scripts\python.exe -m deploy.warm_cache $SERVICE_URL
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
- **Artifact Registry** — storage is billed per GB-month; one ~870 MB image plus a
  little history is cents, and the free allowance absorbs it. Old revisions do
  accumulate, so delete stale tags once a year if you redeploy often.
- **Anthropic API** — the only line item that grows. ~$0.004 per cached-prompt question
  on Haiku 4.5, hard-capped at `MONTHLY_BUDGET_USD=15`.

Realistic steady state: **$0 from Google, single-digit dollars from Anthropic in a busy
month.**

The one way to get surprised is leaving `--min-instances` above 0, which bills for an
always-warm container. Keep it at 0.

---

## Troubleshooting

**`The term 'X' is not recognized`** — a PowerShell problem, not a GCP one. Either
gcloud isn't on PATH (step 2), or you hit the unquoted-parens trap: `--format=value(x)`
makes PowerShell try to execute `x`. Quote it: `--format="value(x)"`.

**`curl` behaves strangely / rejects `-H` or `-d`** — you got `Invoke-WebRequest` via
the alias. Type `curl.exe`.

**`PERMISSION_DENIED` on deploy** — an API isn't enabled (3c) or billing isn't linked
(3b).

**Revision failed to start / "container failed to listen on $PORT"** — read the logs:

```powershell
gcloud run services logs read analytics-agent --region us-west1 --limit 50
```

Usually pydantic rejecting the environment: `missing required settings for
DEPLOY_MODE=deployed`. That means `ANTHROPIC_API_KEY`, `REDIS_URL` or
`CORS_ALLOWED_ORIGIN` didn't arrive. Check what the service actually has:

```powershell
gcloud run services describe analytics-agent --region us-west1 --format="value(spec.template.spec.containers[0].env)"
```

**401 from Anthropic although the key is right** — the classic Windows one. A BOM or
trailing CRLF got into the secret. Re-check with 4c; if it isn't 108, redo 4a with
`[System.IO.File]::WriteAllText`, not `Out-File` or `Set-Content`.

**Secret access denied in the logs** — the IAM binding in 4d didn't apply, or you
re-created the secret afterwards. Re-run that command.

**`/health` works but `/ask` returns 500** — almost always Redis. Deployed mode boots
with a syntactically valid `REDIS_URL` but only *connects* on the first `/ask`. Re-run
the step 1a check against the exact string you stored.

**Browser console says CORS blocked** — the origin on Cloud Run doesn't exactly match
the site. It's an exact string compare: scheme, host, no trailing slash.
`https://carlos-rubio-marroquin.com` — not `http://`, not `www.`, not `.../`.

**The demo answers nothing and the network tab shows `{"disabled": true}`** — either
`DEMO_ENABLED=false` or the monthly budget tripped. Check the `budget:<YYYY-MM>` key
in the Upstash console.

**Everything works but the site still shows "Not live yet"** — you saved the Vercel env
var but didn't redeploy. See 7b.

---

## Redeploying later

After a code change:

```powershell
docker build -f deploy/Dockerfile -t analytics-agent .; docker tag analytics-agent $IMAGE; docker push $IMAGE; gcloud run deploy analytics-agent --image $IMAGE --region us-west1
```

If `$IMAGE` is empty (new window), set it again first:

```powershell
$IMAGE = "us-west1-docker.pkg.dev/$(gcloud config get-value project)/web/analytics-agent:latest"
```

Env vars and secrets persist across deploys — you only pass them again when they change.

After the annual Seattle data refresh, rebuild the baked-in snapshot first:

```powershell
.\.venv\Scripts\python.exe -m ingest.run --source seattle_energy --full-refresh; .\.venv\Scripts\python.exe -m model.seattle_energy.train
```

then commit the regenerated `analytics.db` and model artifact, and rebuild as above.
