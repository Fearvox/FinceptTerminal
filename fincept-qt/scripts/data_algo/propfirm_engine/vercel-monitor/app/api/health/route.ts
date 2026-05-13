import { NextResponse } from 'next/server'
import { supabaseConfigured } from '../../../lib/supabase'
import { webhookSecretConfigured } from '../../../lib/auth'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function GET() {
  return NextResponse.json({
    ok: true,
    mode: 'cloud-readonly-display',
    supabase_configured: supabaseConfigured(),
    webhook_secret_configured: webhookSecretConfigured(),
    mutation_bridge_enabled: false,
    secret_values_recorded: false,
  }, { headers: { 'Cache-Control': 'no-store' } })
}
