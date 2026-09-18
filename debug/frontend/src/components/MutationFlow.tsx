import { useMutation, useQuery } from '@tanstack/react-query'
import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { t } from 'ttag'
import { api, ApiError, stringifyRedacted } from '../api/client'
import type { ActionStatus, JsonValue, PreparedAction } from '../api/types'
import { useDebug } from '../state/debug-context'

interface MutationFlowProps {
  action: Record<string, JsonValue>
  valid: boolean
}

export function MutationFlow({ action, valid }: MutationFlowProps) {
  const { session } = useDebug()
  const signature = useMemo(() => JSON.stringify(action), [action])
  const [prepared, setPrepared] = useState<PreparedAction | null>(null)
  const [preparedSignature, setPreparedSignature] = useState<string | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const submissionLocked = useRef(false)

  const prepare = useMutation({
    mutationFn: () => api<PreparedAction>('/actions/prepare', { method: 'POST', body: action }),
    retry: false,
    onSuccess: (result) => {
      setPrepared(result)
      setPreparedSignature(signature)
      setConfirmation('')
    },
  })
  const execute = useMutation({
    mutationFn: ({ actionId, runId, confirmationText }: { actionId: string; runId: string; confirmationText: string }) =>
      api<ActionStatus>(`/actions/${encodeURIComponent(actionId)}/execute`, {
        method: 'POST',
        body: { run_id: runId, confirmation_text: confirmationText },
      }),
    retry: false,
    onSettled: () => {
      submissionLocked.current = false
    },
  })
  const status = useQuery({
    queryKey: ['action-status', prepared?.action_id],
    queryFn: ({ signal }) => api<ActionStatus>(`/actions/${encodeURIComponent(prepared!.action_id)}`, { signal }),
    enabled: prepared !== null && (
      execute.data?.state === 'running'
      || execute.data?.state === 'unknown'
      || (execute.error instanceof ApiError && execute.error.statusUrl !== null)
    ),
    refetchInterval: (query) => {
      const current = query.state.data as ActionStatus | undefined
      return current && ['succeeded', 'failed', 'expired'].includes(current.state) ? false : 1_000
    },
    gcTime: 0,
    retry: false,
  })

  useEffect(() => {
    setPrepared(null)
    setPreparedSignature(null)
    setConfirmation('')
    prepare.reset()
    execute.reset()
  }, [signature, session.run_id])

  const actionStatus = status.data ?? execute.data
  const expired = prepared ? Date.parse(prepared.expires_at) <= Date.now() : false
  const previewCurrent = preparedSignature === signature
  const currentRun = prepared?.run_id === session.run_id
  const executionStarted = execute.isPending || execute.data !== undefined || execute.error !== null || status.data !== undefined
  const executable = Boolean(prepared && previewCurrent && currentRun && !expired && !executionStarted && session.state === 'ready' && confirmation === prepared.confirmation_text)

  const submitExecution = (event: FormEvent) => {
    event.preventDefault()
    if (!prepared || !executable || submissionLocked.current || execute.isPending) return
    submissionLocked.current = true
    execute.mutate({ actionId: prepared.action_id, runId: prepared.run_id, confirmationText: confirmation })
  }
  return (
    <section className="mutation-flow" aria-label={t`Confirmed write workflow`}>
      <button type="button" onClick={() => prepare.mutate()} disabled={!valid || prepare.isPending || session.state !== 'ready'}>
        {prepare.isPending ? t`Preparing preview…` : t`Prepare write preview`}
      </button>
      {prepare.error && <div className="notice error" role="alert">{prepare.error.message}</div>}
      {prepared && (
        <form className="confirmation-card" onSubmit={submitExecution}>
          <div className="notice warning">
            <strong>{prepared.operation}</strong>
            <p>{t`Preview does not lock the database. Inspect the target immediately before executing.`}</p>
            {prepared.warnings.map((warning) => <p key={warning}>{warning}</p>)}
          </div>
          <pre className="json-view">{stringifyRedacted(prepared.preview)}</pre>
          {!currentRun && <div className="notice error" role="alert">{t`This preview belongs to an earlier worker run. Prepare it again.`}</div>}
          {expired && <div className="notice error" role="alert">{t`This preview expired. Prepare it again.`}</div>}
          <label>
            <span>{t`Type the exact confirmation text`}</span>
            <input
              value={confirmation}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setConfirmation(event.currentTarget.value)}
            />
          </label>
          <code className="confirmation-text">{prepared.confirmation_text}</code>
          <button className="danger" type="submit" disabled={!executable || execute.isPending || submissionLocked.current}>
            {execute.isPending ? t`Executing once…` : t`Execute once`}
          </button>
        </form>
      )}
      {execute.error && <div className="notice error" role="alert">{execute.error.message}</div>}
      {actionStatus && (
        <div className={`notice action-${actionStatus.state}`} role="status">
          <strong>{t`Action status`}: {actionStatus.state}</strong>
          {actionStatus.state === 'unknown' && <p>{t`The write outcome is uncertain. Inspect the target before preparing any new write; this action cannot be retried.`}</p>}
          {(actionStatus.result !== null || actionStatus.error !== null) && (
            <pre className="json-view">{stringifyRedacted(actionStatus.result ?? actionStatus.error ?? null)}</pre>
          )}
        </div>
      )}
    </section>
  )
}
