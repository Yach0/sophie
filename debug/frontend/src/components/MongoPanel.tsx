import { useForm } from '@tanstack/react-form'
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { t } from 'ttag'
import { api } from '../api/client'
import type { JsonValue, MongoQueryResponse } from '../api/types'
import { MutationFlow } from './MutationFlow'
import { ResultGrid } from './ResultGrid'

function parseObject(source: string, label: string): Record<string, JsonValue> {
  const value = JSON.parse(source) as JsonValue
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(t`${label} must be a JSON object`)
  return value as Record<string, JsonValue>
}

function containsPlaceholder(value: string): boolean {
  return /\[(?:redacted|truncated)|<(?:redacted|truncated)>/iu.test(value)
}

export function MongoPanel() {
  const [request, setRequest] = useState<Record<string, JsonValue> | null>(null)
  const [queryError, setQueryError] = useState<string | null>(null)
  const [actionKind, setActionKind] = useState<'mongo.insert_one' | 'mongo.update_one' | 'mongo.delete_one'>('mongo.insert_one')
  const [actionCollection, setActionCollection] = useState('')
  const [actionId, setActionId] = useState('{"$oid":""}')
  const [actionBody, setActionBody] = useState('{}')

  const collections = useQuery({
    queryKey: ['mongo-collections'],
    queryFn: ({ signal }) => api<{ collections: string[] } | string[]>('/mongo/collections', { signal }),
    gcTime: 0,
    retry: false,
  })
  const query = useQuery({
    queryKey: ['inspector', 'mongo', request],
    queryFn: ({ signal }) => api<MongoQueryResponse>('/mongo/query', { method: 'POST', body: request!, signal }),
    enabled: request !== null,
    gcTime: 0,
    retry: false,
  })
  const form = useForm({
    defaultValues: { collection: '', filter: '{}', projection: '', sort: '[]', skip: 0, limit: 50 },
    onSubmit: ({ value }) => {
      try {
        if (!value.collection.trim()) throw new Error(t`Collection is required`)
        if (!Number.isSafeInteger(value.skip) || value.skip < 0) throw new Error(t`Skip must be a non-negative integer`)
        if (!Number.isSafeInteger(value.limit) || value.limit < 1 || value.limit > 200) throw new Error(t`Limit must be between 1 and 200`)
        const projection = value.projection.trim() ? parseObject(value.projection, t`Projection`) : null
        const sort = JSON.parse(value.sort) as JsonValue
        if (!Array.isArray(sort)) throw new Error(t`Sort must be an ordered JSON array`)
        setQueryError(null)
        setRequest({
          collection: value.collection.trim(),
          filter: parseObject(value.filter, t`Filter`),
          projection,
          sort,
          skip: value.skip,
          limit: value.limit,
        })
      } catch (reason) {
        setQueryError(reason instanceof Error ? reason.message : t`Invalid query input`)
      }
    },
  })


  const actionState = useMemo<{ action: Record<string, JsonValue>; error: string | null }>(() => {
    const action: Record<string, JsonValue> = {
      kind: actionKind,
      collection: actionCollection.trim(),
    }
    try {
      if (!actionCollection.trim()) throw new Error(t`Collection is required`)
      if (containsPlaceholder(actionId) || containsPlaceholder(actionBody)) throw new Error(t`Redaction and truncation placeholders cannot be written`)
      if (actionKind === 'mongo.insert_one') {
        action.document = parseObject(actionBody, t`Document`)
      } else {
        action._id = JSON.parse(actionId) as JsonValue
        if (actionKind === 'mongo.update_one') action.update = parseObject(actionBody, t`Update`)
      }
      return { action, error: null }
    } catch (reason) {
      return {
        action,
        error: reason instanceof Error ? reason.message : t`Invalid action input`,
      }
    }
  }, [actionBody, actionCollection, actionId, actionKind])
  const actionValid = actionState.error === null

  const collectionNames = Array.isArray(collections.data) ? collections.data : collections.data?.collections ?? []

  return (
    <section className="inspector-panel" aria-labelledby="mongo-inspector-title">
      <div>
        <h2 id="mongo-inspector-title">{t`Live MongoDB query`}</h2>
        <p>{t`Queries are limited to the selected development database, 200 results, and canonical Extended JSON.`}</p>
        <form onSubmit={(event) => { event.preventDefault(); void form.handleSubmit() }} className="form-grid">
          <form.Field name="collection" validators={{ onChange: ({ value }) => value.trim() ? undefined : t`Collection is required` }}>
            {(field) => <label><span>{t`Collection`}</span><input list="mongo-collections" value={field.state.value} onChange={(event) => field.handleChange(event.currentTarget.value)} /><small>{field.state.meta.errors.join(', ')}</small></label>}
          </form.Field>
          <datalist id="mongo-collections">{collectionNames.map((name) => <option key={name} value={name} />)}</datalist>
          <form.Field name="filter">{(field) => <label className="wide"><span>{t`Filter (Extended JSON)`}</span><textarea value={field.state.value} onChange={(event) => field.handleChange(event.currentTarget.value)} /></label>}</form.Field>
          <form.Field name="projection">{(field) => <label><span>{t`Projection`}</span><textarea value={field.state.value} onChange={(event) => field.handleChange(event.currentTarget.value)} placeholder='{"field":1}' /></label>}</form.Field>
          <form.Field name="sort">{(field) => <label><span>{t`Sort`}</span><textarea value={field.state.value} onChange={(event) => field.handleChange(event.currentTarget.value)} /></label>}</form.Field>
          <form.Field name="skip">{(field) => <label><span>{t`Skip`}</span><input type="number" min={0} value={field.state.value} onChange={(event) => field.handleChange(event.currentTarget.valueAsNumber)} /></label>}</form.Field>
          <form.Field name="limit">{(field) => <label><span>{t`Limit`}</span><input type="number" min={1} max={200} value={field.state.value} onChange={(event) => field.handleChange(event.currentTarget.valueAsNumber)} /></label>}</form.Field>
          <form.Subscribe selector={(state) => [state.canSubmit, state.isSubmitting]}>{([canSubmit, submitting]) => <button type="submit" disabled={!canSubmit || submitting}>{t`Run bounded query`}</button>}</form.Subscribe>
        </form>
        {(queryError || query.error) && <div className="notice error" role="alert">{queryError ?? query.error?.message}</div>}
        {query.data && <><p>{query.data.duration_ms.toFixed(1)} ms · {query.data.has_more ? t`more results available` : t`complete page`} {query.data.truncated ? `· ${t`response truncated`}` : ''}</p><ResultGrid values={query.data.items} /></>}
      </div>

      <div>
        <h2>{t`Single-document write`}</h2>
        <p>{t`Start from an explicit patch or document. A redacted query result is never used as a replacement document.`}</p>
        <div className="form-grid">
          <label><span>{t`Operation`}</span><select value={actionKind} onChange={(event) => setActionKind(event.currentTarget.value as typeof actionKind)}><option value="mongo.insert_one">{t`Insert one`}</option><option value="mongo.update_one">{t`Update one by _id`}</option><option value="mongo.delete_one">{t`Delete one by _id`}</option></select></label>
          <label><span>{t`Collection`}</span><input value={actionCollection} onChange={(event) => setActionCollection(event.currentTarget.value)} /></label>
          {actionKind !== 'mongo.insert_one' && <label className="wide"><span>{t`Exact _id (Extended JSON)`}</span><textarea value={actionId} onChange={(event) => setActionId(event.currentTarget.value)} /></label>}
          {actionKind !== 'mongo.delete_one' && <label className="wide"><span>{actionKind === 'mongo.insert_one' ? t`Document` : t`Update ($set, $unset, $inc only)`}</span><textarea value={actionBody} onChange={(event) => setActionBody(event.currentTarget.value)} /></label>}
        </div>
        {actionState.error && <div className="notice error" role="alert">{actionState.error}</div>}
        <MutationFlow action={actionState.action} valid={actionValid} />
      </div>
    </section>
  )
}
