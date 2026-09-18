import type { Level, SearchFilters } from '../api/types'

const LEVELS: Record<Level, true> = { debug: true, info: true, warning: true, error: true }

function cleanText(value: unknown, maxLength: number): string | undefined {
  if (typeof value !== 'string') return undefined
  const cleaned = value.trim()
  if (!cleaned || cleaned.length > maxLength) return undefined
  return cleaned
}
function utcTimestamp(value: unknown): string | undefined {
  const timestamp = cleanText(value, 64)
  if (!timestamp || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/u.test(timestamp)) return undefined
  return Number.isNaN(Date.parse(timestamp)) ? undefined : timestamp
}


function safeInteger(value: unknown, positiveOnly = false): number | undefined {
  const numeric = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : Number.NaN
  if (!Number.isSafeInteger(numeric) || (positiveOnly && numeric <= 0)) return undefined
  return numeric
}

function booleanValue(value: unknown): boolean | undefined {
  if (value === true || value === 'true') return true
  return undefined
}

export function validateSearch(input: Record<string, unknown>): SearchFilters {
  const level = cleanText(input.level, 16)
  return {
    run: cleanText(input.run, 128),
    trace: cleanText(input.trace, 128),
    chat: input.chat === undefined ? undefined : safeInteger(input.chat),
    level: level && LEVELS[level as Level] ? (level as Level) : undefined,
    from: utcTimestamp(input.from),
    to: utcTimestamp(input.to),
    event: input.event === undefined ? undefined : safeInteger(input.event, true),
    q: cleanText(input.q, 256),
    errors: booleanValue(input.errors),
    polling: booleanValue(input.polling),
  }
}
