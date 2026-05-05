# Fincept Propfirm Monitor — Vercel + Supabase

Cloud monitor for the Propfirm/Fusion TradingView alert lane.

It replaces the local-only JSONL panel with:

```text
TradingView webhook
  → Vercel /api/tv-signal?secret=...
  → sanitize + redact
  → Supabase public.propfirm_alerts
  → Vercel dashboard + /api/alerts
```

Boundary: **read-only display**. No broker APIs, no order execution, no trade suggestions.

## 1. Create Supabase table

Run `supabase/schema.sql` in the Supabase SQL editor.

The table has RLS enabled and intentionally grants no anon/authenticated policies. The Vercel API must use `SUPABASE_SERVICE_ROLE_KEY` server-side only.

## 2. Configure environment

Copy `.env.example` to `.env.local` for local dev, or set these in Vercel:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `TV_WEBHOOK_SECRET`
- `DASHBOARD_USER`
- `DASHBOARD_PASSWORD`

Use `printf`, not `echo`, when adding Vercel env vars:

```bash
printf "https://your-project.supabase.co" | vercel env add SUPABASE_URL production
printf "service-role-key" | vercel env add SUPABASE_SERVICE_ROLE_KEY production
printf "webhook-secret" | vercel env add TV_WEBHOOK_SECRET production
printf "fincept" | vercel env add DASHBOARD_USER production
printf "dashboard-password" | vercel env add DASHBOARD_PASSWORD production
```

## 3. Local verification

```bash
npm install
npm run typecheck
npm run build
npm run dev
```

Health check:

```bash
curl -s http://127.0.0.1:3000/api/health
```

Webhook smoke test:

```bash
curl -s -X POST "http://127.0.0.1:3000/api/tv-signal?secret=$TV_WEBHOOK_SECRET"   -H "Content-Type: application/json"   -d '{"symbol":"NQ","action":"entry","event":"long","price":17654.25,"api_key":"should-redact"}'
```

## 4. Deploy

From this directory:

```bash
vercel link --yes
vercel --prod --yes
```

TradingView webhook URL:

```text
https://YOUR-APP.vercel.app/api/tv-signal?secret=YOUR_SECRET
```

Dashboard:

```text
https://YOUR-APP.vercel.app/
```

`/` and `/api/alerts` are protected with Basic Auth when `DASHBOARD_PASSWORD` is set. `/api/tv-signal` is protected by `TV_WEBHOOK_SECRET`.

## Security notes

- Never use `NEXT_PUBLIC_` for Supabase service-role key.
- Never store raw secrets from alert payloads; the API redacts secret-like keys and values before insert.
- Keep Supabase RLS enabled. The browser should not query Supabase directly.
- Rotate `TV_WEBHOOK_SECRET` if it appears in screenshots, logs, or shared chat.
