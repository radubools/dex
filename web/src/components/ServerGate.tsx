import { useEffect, useState, type ReactNode } from 'react'
import { ServerPicker } from './ServerPicker'

/** How long the server gets to answer, and how often it is tried again after that. */
const ANSWER_MS = 4000
const RETRY_MS = 5000

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/** Whether dex answers through this page's origin: a 401 is an answer too. */
async function answers(): Promise<boolean> {
  try {
    const response = await fetch('/api/auth/me', {
      cache: 'no-store',
      credentials: 'same-origin',
      signal: AbortSignal.timeout(ANSWER_MS),
    })
    return response.status < 500
  } catch {
    return false
  }
}

/**
 * The app, once its server answers; the server list until it does.
 *
 * Held back rather than mounted behind a notice: a server that stopped
 * answering leaves every request hanging, a blank page for as long as the
 * connection takes to give up, and those requests use up the browser's
 * connections to this origin, the picker's included. The app starts by itself
 * if the server comes back.
 */
export function ServerGate({ children }: { children: ReactNode }) {
  const [reach, setReach] = useState<'checking' | 'up' | 'down'>('checking')

  useEffect(() => {
    let live = true
    void (async () => {
      while (live) {
        if (await answers()) {
          if (live) setReach('up')
          return
        }
        if (live) setReach('down')
        await sleep(RETRY_MS)
      }
    })()
    return () => { live = false }
  }, [])

  if (reach === 'up') return <>{children}</>
  if (reach === 'checking') return <div className="gate" aria-busy="true" />
  return (
    <div className="gate">
      <div className="gate-card">
        <h1>This dex server is not answering</h1>
        <p className="muted small">Choose another one. This page carries on by itself if this one comes back.</p>
        <ServerPicker />
      </div>
    </div>
  )
}
