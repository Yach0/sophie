import { createFileRoute } from '@tanstack/react-router'
import { PerformancePanel } from '../components/PerformancePanel'

export const Route = createFileRoute('/performance')({ component: PerformancePanel })
