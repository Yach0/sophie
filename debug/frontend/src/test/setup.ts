import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

class TestResizeObserver implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

Object.defineProperty(globalThis, 'ResizeObserver', { value: TestResizeObserver, configurable: true })
Object.defineProperty(HTMLElement.prototype, 'scrollTo', { value: vi.fn(), configurable: true })
Object.defineProperty(window, 'scrollTo', { value: vi.fn(), configurable: true })
Object.defineProperty(navigator, 'clipboard', {
  value: { writeText: vi.fn().mockResolvedValue(undefined) },
  configurable: true,
})
