import { feedPayload, listAlerts } from '../lib/alerts'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'
export const revalidate = 0

function statusClass(status: string) {
  if (status === 'error') return 'alert error'
  if (status === 'rejected') return 'alert rejected'
  return 'alert'
}

export default async function Page() {
  const result = await listAlerts(80)
  const payload = feedPayload(result.events)
  const latest = result.events[0]?.received_at || '—'

  return (
    <main className="main">
      <meta httpEquiv="refresh" content="15" />
      <section className="header">
        <div>
          <h1>Fincept Propfirm Monitor</h1>
          <p>
            Supabase-backed TradingView alert monitor. Sanitized facts only. No broker calls,
            no order placement, no trade suggestions.
          </p>
        </div>
        <div className="badge">cloud readonly feed</div>
      </section>

      {!result.configured && (
        <section className="notice">
          Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in Vercel,
          then run the SQL in supabase/schema.sql.
        </section>
      )}
      {result.error && result.configured && <section className="notice">Storage error: {result.error}</section>}

      <dl className="stats">
        <div className="stat"><dt>Events</dt><dd>{payload.event_count}</dd></div>
        <div className="stat"><dt>Latest</dt><dd>{latest}</dd></div>
        <div className="stat"><dt>Mode</dt><dd>read-only</dd></div>
        <div className="stat"><dt>Boundary</dt><dd>no trades</dd></div>
      </dl>

      <section className="alerts" aria-label="TradingView alert feed">
        {result.events.length === 0 ? (
          <article className="alert">
            <div className="alert-title">No alerts stored yet</div>
            <p>POST TradingView JSON to /api/tv-signal?secret=*** after Supabase is configured.</p>
          </article>
        ) : result.events.map((event) => (
          <article className={statusClass(event.status)} key={event.id}>
            <div className="alert-head">
              <div>
                <div className="alert-title">{event.kind.toUpperCase()} · {event.symbol}</div>
                <p>{event.no_trade_boundary}</p>
              </div>
              <div className="alert-time">{event.received_at}</div>
            </div>
            <div className="kv">
              <div><span>Action</span><strong>{event.action || '—'}</strong></div>
              <div><span>Event</span><strong>{event.event || '—'}</strong></div>
              <div><span>Side</span><strong>{event.side || '—'}</strong></div>
              <div><span>Price</span><strong>{event.price || '—'}</strong></div>
              <div><span>Status</span><strong>{event.status}</strong></div>
            </div>
            <pre>{JSON.stringify({ payload: event.payload, response: event.response, error: event.error }, null, 2)}</pre>
          </article>
        ))}
      </section>
      <p className="footer">Refreshes every 15s. API: /api/alerts. Webhook: POST /api/tv-signal?secret=***.</p>
    </main>
  )
}
