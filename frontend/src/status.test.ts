import { describe, expect, it } from 'vitest'
import { statusTone } from './status'

describe('statusTone', () => {
  it('memetakan status ke warna sesuai AC', () => {
    expect(statusTone('extracted')).toBe('green')
    expect(statusTone('normalized')).toBe('green')
    expect(statusTone('needs_review')).toBe('yellow')
    expect(statusTone('conflict')).toBe('yellow')
    expect(statusTone('error')).toBe('red')
    expect(statusTone('missing')).toBe('red')
    expect(statusTone('inferred')).toBe('blue')
    expect(statusTone('user_provided')).toBe('gray')
  })
})
