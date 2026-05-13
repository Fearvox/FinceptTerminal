-- Fincept Propfirm Monitor — Supabase schema
-- Run in the Supabase SQL editor. This table stores sanitized alert facts only.

create table if not exists public.propfirm_alerts (
  id uuid primary key,
  schema_version integer not null default 1,
  received_at timestamptz not null default now(),
  kind text not null default 'tv-signal',
  status text not null check (status in ('accepted', 'rejected', 'error')),
  symbol text not null default '—',
  action text,
  event text,
  side text,
  price text,
  payload jsonb not null default '{}'::jsonb,
  response jsonb not null default '{}'::jsonb,
  error text,
  event_hash text not null,
  no_trade_boundary text not null default 'NO TRADES EXECUTED, PLACED, OR SUGGESTED BY THIS MONITOR'
);

create index if not exists propfirm_alerts_received_at_idx on public.propfirm_alerts (received_at desc);
create index if not exists propfirm_alerts_symbol_received_at_idx on public.propfirm_alerts (symbol, received_at desc);
create index if not exists propfirm_alerts_status_received_at_idx on public.propfirm_alerts (status, received_at desc);
create index if not exists propfirm_alerts_payload_gin_idx on public.propfirm_alerts using gin (payload);

alter table public.propfirm_alerts enable row level security;

-- The Vercel API uses SUPABASE_SERVICE_ROLE_KEY server-side. Do not expose that key to browsers.
-- No anon/authenticated policies are intentionally created: direct browser reads/writes stay blocked by RLS.
revoke all on public.propfirm_alerts from anon, authenticated;
