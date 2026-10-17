export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'

export interface Health {
  status: string
  version: string
  model: string
  ai_enabled: boolean
  daily_ai_budget_left: number
}

export async function getHealth(timeoutMs = 10_000): Promise<Health> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const res = await fetch(`${API_BASE}/api/health`, { signal: ctrl.signal })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return (await res.json()) as Health
  } finally {
    clearTimeout(timer)
  }
}
