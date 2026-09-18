import { createFileRoute } from '@tanstack/react-router'
import { t } from 'ttag'
import { AiCachePanel } from '../components/AiCachePanel'
import { EventWorkspace } from '../components/EventWorkspace'

export const Route = createFileRoute('/ai-cache')({
  component: () => <div className="route-stack"><AiCachePanel /><EventWorkspace title={t`AI cache activity`} description={t`Derived Redis activity for exact Sophie cache key families, linked to the originating Redis span.`} categories={['ai_cache']} /></div>,
})
