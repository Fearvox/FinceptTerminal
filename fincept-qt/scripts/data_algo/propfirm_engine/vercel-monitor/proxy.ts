import { NextRequest, NextResponse } from 'next/server'

function unauthorized(message = 'Authentication required') {
  return new NextResponse(message, {
    status: 401,
    headers: { 'WWW-Authenticate': 'Basic realm="Fincept Propfirm Monitor"' },
  })
}

export function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl
  if (pathname.startsWith('/api/tv-signal') || pathname.startsWith('/api/health')) {
    return NextResponse.next()
  }

  const password = process.env.DASHBOARD_PASSWORD
  const user = process.env.DASHBOARD_USER || 'fincept'
  if (!password) {
    if (process.env.NODE_ENV === 'production') {
      return new NextResponse('DASHBOARD_PASSWORD is required in production', { status: 503 })
    }
    return NextResponse.next()
  }

  const auth = req.headers.get('authorization')
  if (!auth?.startsWith('Basic ')) return unauthorized()
  let decoded = ''
  try {
    decoded = atob(auth.slice('Basic '.length))
  } catch {
    return unauthorized('Invalid credentials')
  }
  const index = decoded.indexOf(':')
  const actualUser = index >= 0 ? decoded.slice(0, index) : ''
  const actualPassword = index >= 0 ? decoded.slice(index + 1) : ''
  if (actualUser !== user || actualPassword !== password) return unauthorized('Invalid credentials')
  return NextResponse.next()
}

export const config = {
  matcher: ['/', '/api/alerts/:path*'],
}
