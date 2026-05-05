import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Fincept Propfirm Monitor',
  description: 'Read-only Supabase-backed monitor for TradingView alert facts.',
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
