import { NextRequest, NextResponse } from 'next/server'
import { feedPayload, listAlerts } from '../../../lib/alerts'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function GET(req: NextRequest) {
  const limit = Number(req.nextUrl.searchParams.get('limit') || 80)
  const result = await listAlerts(limit)
  if (!result.configured) {
    return NextResponse.json({ error: { code: result.error } }, { status: 503, headers: { 'Cache-Control': 'no-store' } })
  }
  if (result.error) {
    return NextResponse.json({ error: { code: 'storage_error', message: result.error } }, { status: 503, headers: { 'Cache-Control': 'no-store' } })
  }
  return NextResponse.json(feedPayload(result.events), { headers: { 'Cache-Control': 'no-store' } })
}
