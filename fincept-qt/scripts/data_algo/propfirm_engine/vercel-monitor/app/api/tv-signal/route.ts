import { NextRequest, NextResponse } from 'next/server'
import { hasValidWebhookSecret, webhookSecretConfigured } from '../../../lib/auth'
import { insertAlert, makeAlertEvent, parseWebhookBody } from '../../../lib/alerts'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

function json(body: unknown, status = 200) {
  return NextResponse.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  })
}

export async function POST(req: NextRequest) {
  if (!webhookSecretConfigured()) {
    return json({ error: { code: 'webhook_secret_not_configured' } }, 503)
  }
  if (!hasValidWebhookSecret(req)) {
    return json({ error: { code: 'unauthorized' } }, 401)
  }

  let payload: Record<string, unknown>
  try {
    payload = parseWebhookBody(await req.text())
  } catch (err) {
    const code = err instanceof Error ? err.message : 'invalid_payload'
    return json({ error: { code } }, code === 'body_too_large' ? 413 : 400)
  }

  const event = makeAlertEvent({ kind: 'tv-signal', status: 'accepted', payload })
  const inserted = await insertAlert(event)
  if (!inserted.ok) return json({ error: { code: 'storage_error', message: inserted.error } }, 503)

  return json({ ok: true, id: event.id, no_trade_boundary: event.no_trade_boundary }, 201)
}

export async function GET() {
  return json({ ok: true, endpoint: 'POST /api/tv-signal', mutation_bridge_enabled: false })
}
