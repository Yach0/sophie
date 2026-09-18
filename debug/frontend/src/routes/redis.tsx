import { createFileRoute } from '@tanstack/react-router'
import { t } from 'ttag'
import { EventWorkspace } from '../components/EventWorkspace'
import { RedisPanel } from '../components/RedisPanel'

export const Route = createFileRoute('/redis')({
  component: () => <div className="route-stack"><RedisPanel /><EventWorkspace title={t`Observed Redis commands`} description={t`Application and FSM clients, selected databases, WATCH calls, and aggregate pipeline timing.`} categories={['redis']} /></div>,
})
