import { useForm } from '@tanstack/react-form'
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { t } from 'ttag'
import { api, stringifyRedacted } from '../api/client'
import type { JsonValue, RedisQueryResponse } from '../api/types'
import { MutationFlow } from './MutationFlow'
import { ResultGrid } from './ResultGrid'

const redisCommands = ['SET', 'DEL', 'UNLINK', 'HSET', 'HDEL', 'ZADD', 'ZREM', 'EXPIRE', 'PERSIST'] as const

type RedisReadOp = 'scan' | 'string' | 'hash' | 'set' | 'list' | 'zset'

function parseRedisBytes(value: string): JsonValue {
  const trimmed = value.trim()
  if (trimmed.startsWith('{')) {
    const parsed = JSON.parse(trimmed) as JsonValue
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || typeof parsed.base64 !== 'string') {
      throw new Error(t`Binary values must use {"base64":"…"}`)
    }
    return parsed
  }
  return value
}

export function RedisPanel() {
  const [request, setRequest] = useState<Record<string, JsonValue> | null>(null)
  const [inputError, setInputError] = useState<string | null>(null)
  const [command, setCommand] = useState<(typeof redisCommands)[number]>('SET')
  const [key, setKey] = useState('')
  const [args, setArgs] = useState('["value","EX",60]')

  const query = useQuery({
    queryKey: ['inspector', 'redis', request],
    queryFn: ({ signal }) => api<RedisQueryResponse>('/redis/query', { method: 'POST', body: request!, signal }),
    enabled: request !== null,
    gcTime: 0,
    retry: false,
  })
  const form = useForm({
    defaultValues: { op: 'scan' as RedisReadOp, key: '', pattern: '*', cursor: '0', offset: 0, limit: 50 },
    onSubmit: ({ value }) => {
      try {
        setInputError(null)
        const cursor = Number(value.cursor)
        if (!Number.isSafeInteger(cursor) || cursor < 0) throw new Error(t`Cursor must be a non-negative integer`)
        if (!Number.isSafeInteger(value.offset) || value.offset < 0) throw new Error(t`Offset must be a non-negative integer`)
        if (!Number.isSafeInteger(value.limit) || value.limit < 1 || value.limit > 200) throw new Error(t`Limit must be between 1 and 200`)
        if (value.op === 'scan') {
          setRequest({ op: 'scan', pattern: parseRedisBytes(value.pattern), cursor, limit: value.limit })
        } else {
          if (!value.key) throw new Error(t`Key is required`)
          const keyValue = parseRedisBytes(value.key)
          if (value.op === 'hash' || value.op === 'set') {
            setRequest({ op: value.op, key: keyValue, cursor, limit: value.limit })
          } else {
            setRequest({ op: value.op, key: keyValue, offset: value.offset, limit: value.limit })
          }
        }
      } catch (reason) {
        setInputError(reason instanceof Error ? reason.message : t`Invalid Redis query`)
      }
    },
  })

  const actionState = useMemo(() => {
    try {
      if (!key) throw new Error(t`Key is required`)
      const parsedArgs = JSON.parse(args) as JsonValue
      if (!Array.isArray(parsedArgs)) throw new Error(t`Arguments must be a JSON array`)
      const keyValue = parseRedisBytes(key)
      return {
        action: { kind: 'redis.command', command, args: [keyValue, ...parsedArgs] } satisfies Record<string, JsonValue>,
        error: null as string | null,
      }
    } catch (reason) {
      return {
        action: { kind: 'redis.command', command, args: [] } satisfies Record<string, JsonValue>,
        error: reason instanceof Error ? reason.message : t`Invalid Redis command`,
      }
    }
  }, [args, command, key])

  const resultValues = query.data
    ? Array.isArray(query.data.data) ? query.data.data : [query.data.data]
    : []

  return (
    <section className="inspector-panel" aria-labelledby="redis-inspector-title">
      <div>
        <h2 id="redis-inspector-title">{t`Live Redis inspector`}</h2>
        <p>{t`Cursor scans are bounded, live, and non-atomic. KEYS, MONITOR, scripts, and admin commands are unavailable.`}</p>
        <form onSubmit={(event) => { event.preventDefault(); void form.handleSubmit() }} className="form-grid">
          <form.Field name="op">{(field) => <label><span>{t`Read type`}</span><select value={field.state.value} onChange={(event) => field.handleChange(event.currentTarget.value as RedisReadOp)}><option value="scan">{t`Scan keys`}</option><option value="string">{t`String`}</option><option value="hash">{t`Hash`}</option><option value="set">{t`Set`}</option><option value="list">{t`List`}</option><option value="zset">{t`Sorted set`}</option></select></label>}</form.Field>
          <form.Subscribe selector={(state) => state.values.op}>{(op) => op === 'scan' ? <form.Field name="pattern">{(field) => <label><span>{t`Pattern`}</span><input value={field.state.value} onChange={(event) => field.handleChange(event.currentTarget.value)} /></label>}</form.Field> : <form.Field name="key">{(field) => <label><span>{t`Key or base64 object`}</span><input value={field.state.value} onChange={(event) => field.handleChange(event.currentTarget.value)} /></label>}</form.Field>}</form.Subscribe>
          <form.Field name="cursor">{(field) => <label><span>{t`Cursor`}</span><input value={field.state.value} onChange={(event) => field.handleChange(event.currentTarget.value)} /></label>}</form.Field>
          <form.Field name="offset">{(field) => <label><span>{t`Offset`}</span><input type="number" min={0} value={field.state.value} onChange={(event) => field.handleChange(event.currentTarget.valueAsNumber)} /></label>}</form.Field>
          <form.Field name="limit">{(field) => <label><span>{t`Limit`}</span><input type="number" min={1} max={200} value={field.state.value} onChange={(event) => field.handleChange(event.currentTarget.valueAsNumber)} /></label>}</form.Field>
          <form.Subscribe selector={(state) => [state.canSubmit, state.isSubmitting]}>{([canSubmit, submitting]) => <button type="submit" disabled={!canSubmit || submitting}>{t`Inspect key data`}</button>}</form.Subscribe>
        </form>
        {(inputError || query.error) && <div className="notice error" role="alert">{inputError ?? query.error?.message}</div>}
        {query.data && (
          <div>
            <p>{query.data.type} · TTL {query.data.ttl ?? '—'} · {query.data.duration_ms.toFixed(1)} ms {query.data.has_more ? `· ${t`more available`}` : ''} {query.data.truncated ? `· ${t`response truncated`}` : ''}</p>
            {['none', 'missing'].includes(query.data.type) || query.data.ttl === -2
              ? <div className="empty-state">{t`The key is missing or expired.`}</div>
              : <ResultGrid values={resultValues} />}
          </div>
        )}
      </div>

      <div>
        <h2>{t`Allowed single-key command`}</h2>
        <div className="form-grid">
          <label><span>{t`Command`}</span><select value={command} onChange={(event) => setCommand(event.currentTarget.value as typeof command)}>{redisCommands.map((value) => <option key={value}>{value}</option>)}</select></label>
          <label><span>{t`One key`}</span><input value={key} onChange={(event) => setKey(event.currentTarget.value)} /></label>
          <label className="wide"><span>{t`Typed arguments after the key (JSON array)`}</span><textarea value={args} onChange={(event) => setArgs(event.currentTarget.value)} /></label>
        </div>
        {actionState.error && <div className="notice error" role="alert">{actionState.error}</div>}
        <details><summary>{t`Normalized request preview`}</summary><pre className="json-view">{stringifyRedacted(actionState.action)}</pre></details>
        <MutationFlow action={actionState.action} valid={actionState.error === null} />
      </div>
    </section>
  )
}
