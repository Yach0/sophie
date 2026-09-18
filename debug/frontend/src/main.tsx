import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createRouter, RouterProvider } from '@tanstack/react-router'
import { createRoot } from 'react-dom/client'
import { authenticate, takeFragmentToken } from './api/client'
import { AuthGate } from './components/AuthGate'
import './i18n'
import { routeTree } from './routeTree.gen'
import { DebugProvider } from './state/debug-context'
import './styles.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
      gcTime: 60_000,
    },
    mutations: {
      retry: false,
    },
  },
})
const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

const rootElement = document.getElementById('root')
if (!rootElement) throw new Error('Debugger root element is missing')
const root = createRoot(rootElement)


async function bootstrap() {
  let fragmentToken = takeFragmentToken()
  if (fragmentToken) {
    try {
      await authenticate(fragmentToken)
    } catch {
      // AuthGate will offer the local token-entry form without retaining the failed fragment.
    } finally {
      fragmentToken = null
    }
  }
  root.render(
    <QueryClientProvider client={queryClient}>
      <AuthGate>
        {(initialSession) => (
          <DebugProvider initialSession={initialSession}>
            <RouterProvider router={router} />
          </DebugProvider>
        )}
      </AuthGate>
    </QueryClientProvider>,
  )
}

void bootstrap()
