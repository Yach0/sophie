import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { t } from 'ttag'
import { api, stringifyRedacted } from '../api/client'
import type { AiCacheResponse, JsonValue } from '../api/types'
import { MutationFlow } from './MutationFlow'
import { ResultGrid } from './ResultGrid'

type CacheKind = 'messages' | 'tools' | 'pricing'
type ClearKind = 'messages' | 'tools' | 'context' | 'pricing'

export function AiCachePanel() {
  const queryClient = useQueryClient()
  const [chatInput, setChatInput] = useState('')
  const [kind, setKind] = useState<CacheKind>('messages')
  const [clearKind, setClearKind] = useState<ClearKind>('messages')
  const [position, setPosition] = useState(0)
  const chatTid = Number(chatInput)
  const validChat = Number.isSafeInteger(chatTid) && chatInput.trim() !== ''

  useEffect(() => {
    void queryClient.cancelQueries({ queryKey: ['inspector', 'ai-cache'] })
    queryClient.removeQueries({ queryKey: ['inspector', 'ai-cache'] })
  }, [chatInput, kind, queryClient])

  const inspector = useQuery({
    queryKey: ['inspector', 'ai-cache', kind, chatTid, position],
    queryFn: ({ signal }) => {
      if (kind === 'pricing') return api<AiCacheResponse>('/ai-cache/pricing', { signal })
      const positionName = kind === 'messages' ? 'offset' : 'cursor'
      return api<AiCacheResponse>(`/ai-cache/${chatTid}?kind=${kind}&${positionName}=${position}&limit=200`, { signal })
    },
    enabled: kind === 'pricing' || validChat,
    gcTime: 0,
    retry: false,
  })

  const clearAction = useMemo<Record<string, JsonValue>>(() => {
    const action: Record<string, JsonValue> = { kind: 'ai_cache.clear', cache_kind: clearKind }
    if (clearKind !== 'pricing') action.chat_tid = validChat ? chatTid : 0
    return action
  }, [chatTid, clearKind, validChat])
  const entries = inspector.data?.entries ?? (inspector.data?.value !== undefined ? [inspector.data.value] : [])
  const invalidEntries = inspector.data?.invalid_entries ?? (inspector.data?.invalid_entry ? [inspector.data.invalid_entry] : [])
  const isMissing = inspector.data && (inspector.data.missing === true || inspector.data.ttl === -2)
  const isEmpty = inspector.data && !isMissing && entries.length === 0 && invalidEntries.length === 0

  return (
    <section className="inspector-panel" aria-labelledby="ai-cache-title">
      <div>
        <h2 id="ai-cache-title">{t`Stored AI cache data`}</h2>
        <p>{t`This read-only view does not refill pricing, refresh TTLs, prune malformed tools, or evaluate feature rollout membership.`}</p>
        <div className="form-grid">
          <label><span>{t`Subview`}</span><select value={kind} onChange={(event) => { setKind(event.currentTarget.value as CacheKind); setPosition(0) }}><option value="messages">{t`Messages`}</option><option value="tools">{t`Tool exchanges`}</option><option value="pricing">{t`Global pricing`}</option></select></label>
          {kind !== 'pricing' && <label><span>{t`Telegram chat ID`}</span><input inputMode="numeric" value={chatInput} onChange={(event) => { setChatInput(event.currentTarget.value); setPosition(0) }} /></label>}
        </div>
        {kind === 'tools' && <div className="notice info">{t`Replay status is not evaluated. ai_chatbot_tool_history controls replay use, not whether stored exchanges are visible.`}</div>}
        <div className="notice info">{t`Provider prompt caching is not reported. Sophie has no separate response, media, or transcription cache.`}</div>
        {inspector.isPending && (kind === 'pricing' || validChat) && <p role="status">{t`Reading bounded cache data…`}</p>}
        {inspector.error && <div className="notice error" role="alert">{inspector.error.message}</div>}
        {isMissing && <div className="empty-state">{t`The selected cache key is missing or expired.`}</div>}
        {isEmpty && <div className="empty-state">{t`The selected cache exists but contains no entries.`}</div>}
        {inspector.data && !isMissing && (
          <>
            <dl className="fact-grid"><div><dt>{t`Exact key`}</dt><dd><code>{stringifyRedacted(inspector.data.key ?? null)}</code></dd></div><div><dt>{t`Live TTL`}</dt><dd>{inspector.data.ttl ?? '—'}</dd></div><div><dt>{t`Configured TTL`}</dt><dd>{inspector.data.configured_ttl_seconds ?? '—'}</dd></div><div><dt>{t`Count`}</dt><dd>{inspector.data.count ?? entries.length}</dd></div></dl>
            {inspector.data.truncated && <div className="notice warning">{t`The bounded cache response was truncated.`}</div>}
            {entries.length > 0 && <ResultGrid values={entries} label={t`AI cache entries`} />}
            {invalidEntries.length > 0 && <div className="notice warning"><strong>{t`Malformed stored entries were not modified`}</strong><ResultGrid values={invalidEntries} label={t`Malformed AI cache entries`} /></div>}
            {inspector.data.has_more && kind !== 'pricing' && (
              <button
                type="button"
                onClick={() => setPosition(kind === 'tools' ? inspector.data?.next_cursor ?? 0 : position + entries.length)}
              >
                {t`Load next bounded page`}
              </button>
            )}
          </>
        )}
      </div>

      <div>
        <h2>{t`Clear an exact cache scope`}</h2>
        <p>{t`Context clears message and tool context only. It never invokes /aireset and never removes durable AI memory.`}</p>
        <div className="form-grid">
          <label><span>{t`Scope`}</span><select value={clearKind} onChange={(event) => setClearKind(event.currentTarget.value as ClearKind)}><option value="messages">{t`Messages for chat`}</option><option value="tools">{t`Tools for chat`}</option><option value="context">{t`Both context stores for chat`}</option><option value="pricing">{t`Global pricing`}</option></select></label>
          {clearKind !== 'pricing' && <label><span>{t`Telegram chat ID`}</span><input inputMode="numeric" value={chatInput} onChange={(event) => { setChatInput(event.currentTarget.value); setPosition(0) }} /></label>}
        </div>
        <MutationFlow action={clearAction} valid={clearKind === 'pricing' || validChat} />
      </div>
    </section>
  )
}
