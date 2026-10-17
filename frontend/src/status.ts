import type { FieldStatus } from './types'

export type StatusTone = 'green' | 'yellow' | 'red' | 'blue' | 'gray'

/** Pemetaan status -> warna (US-03 AC1). */
export function statusTone(status: FieldStatus): StatusTone {
  switch (status) {
    case 'extracted':
    case 'normalized':
      return 'green'
    case 'needs_review':
    case 'conflict':
      return 'yellow'
    case 'error':
    case 'missing':
      return 'red'
    case 'inferred':
      return 'blue'
    default:
      return 'gray'
  }
}
