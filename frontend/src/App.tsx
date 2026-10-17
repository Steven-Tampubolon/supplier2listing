import { useEffect, useState } from 'react'
import { getHealth, type Health } from './api'

type ServerState = 'waking' | 'ready' | 'down'

const POLL_MS = 3_000
const MAX_WAIT_MS = 120_000

export default function App() {
  const [state, setState] = useState<ServerState>('waking')
  const [health, setHealth] = useState<Health | null>(null)

  useEffect(() => {
    let cancelled = false
    const started = Date.now()

    async function poll() {
      try {
        const h = await getHealth()
        if (cancelled) return
        setHealth(h)
        setState('ready')
      } catch {
        if (cancelled) return
        if (Date.now() - started > MAX_WAIT_MS) {
          setState('down')
        } else {
          setTimeout(poll, POLL_MS)
        }
      }
    }
    poll()
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="text-2xl font-bold">Supplier2Listing</h1>
      <p className="mt-1 text-slate-600">
        Ubah file supplier berantakan menjadi katalog siap tayang, tanpa menebak data.
      </p>

      <p className="mt-4 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
        Gunakan hanya data sintetis atau publik. Jangan unggah data pribadi, kesehatan, atau
        keuangan yang sensitif.
      </p>

      <section className="mt-6 rounded border p-4">
        {state === 'waking' && <p>Menyalakan server, sekitar 1 menit…</p>}
        {state === 'down' && (
          <p className="text-red-700">
            Server belum merespons. Coba muat ulang halaman beberapa saat lagi.
          </p>
        )}
        {state === 'ready' && health && (
          <ul className="text-sm">
            <li>Server: siap (v{health.version})</li>
            <li>Model: {health.model}</li>
            <li>AI Live: {health.ai_enabled ? 'tersedia' : 'nonaktif (gunakan Replay/Aturan)'}</li>
          </ul>
        )}
      </section>
    </main>
  )
}
