import { createFileRoute } from '@tanstack/react-router'
import { t } from 'ttag'
import { EventWorkspace } from '../components/EventWorkspace'
import { MongoPanel } from '../components/MongoPanel'

export const Route = createFileRoute('/mongo')({
  component: () => <div className="route-stack"><MongoPanel /><EventWorkspace title={t`Observed MongoDB wire commands`} description={t`Driver attempts, results, errors, collections, durations, and trace links.`} categories={['mongo']} /></div>,
})
