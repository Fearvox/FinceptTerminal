import { timingSafeEqual } from 'crypto'
import type { NextRequest } from 'next/server'

function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a)
  const right = Buffer.from(b)
  if (left.length !== right.length) return false
  return timingSafeEqual(left, right)
}

export function hasValidWebhookSecret(req: NextRequest): boolean {
  const expected = process.env.TV_WEBHOOK_SECRET
  if (!expected) return false
  const urlSecret = req.nextUrl.searchParams.get('secret') || ''
  const headerSecret = req.headers.get('x-tv-webhook-secret') || ''
  return safeEqual(urlSecret, expected) || safeEqual(headerSecret, expected)
}

export function webhookSecretConfigured(): boolean {
  return Boolean(process.env.TV_WEBHOOK_SECRET)
}
