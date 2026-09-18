import { createFileRoute } from '@tanstack/react-router'
import { t } from 'ttag'
import { EventWorkspace } from '../components/EventWorkspace'

export const Route = createFileRoute('/logs')({
  component: () => <EventWorkspace title={t`Logs and Python output`} description={t`Structured records, contextual Python stdout/stderr lines, exceptions, and trace links. Native FD writes remain terminal-only exit diagnostics.`} categories={['log']} defaultErrors />,
})
