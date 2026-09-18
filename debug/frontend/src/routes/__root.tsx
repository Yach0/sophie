import { createRootRoute } from '@tanstack/react-router'
import { AppShell } from '../components/AppShell'
import { validateSearch } from './-search'

export const Route = createRootRoute({
  validateSearch,
  component: AppShell,
})
