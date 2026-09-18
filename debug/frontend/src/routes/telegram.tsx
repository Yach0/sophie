import { createFileRoute } from '@tanstack/react-router'
import { t } from 'ttag'
import { EventWorkspace } from '../components/EventWorkspace'

export const Route = createFileRoute('/telegram')({
  component: () => <EventWorkspace title={t`Telegram`} description={t`Incoming updates, outgoing Bot API calls, and correlated update work. Background follow-ups retain the trace but remain visibly marked by origin.`} categories={['telegram']} telegram />,
})
