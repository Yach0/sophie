import type { Route as rootRouteImport } from './routes/__root'
import type { Route as IndexRouteImport } from './routes/index'
import type { Route as TelegramRouteImport } from './routes/telegram'
import type { Route as MongoRouteImport } from './routes/mongo'
import type { Route as RedisRouteImport } from './routes/redis'
import type { Route as AiCacheRouteImport } from './routes/ai-cache'
import type { Route as PerformanceRouteImport } from './routes/performance'
import type { Route as LogsRouteImport } from './routes/logs'

declare module '@tanstack/react-router' {
  interface FileRoutesByPath {
    '/': {
      id: '/'
      path: '/'
      fullPath: '/'
      preLoaderRoute: typeof IndexRouteImport
      parentRoute: typeof rootRouteImport
    }
    '/telegram': {
      id: '/telegram'
      path: '/telegram'
      fullPath: '/telegram'
      preLoaderRoute: typeof TelegramRouteImport
      parentRoute: typeof rootRouteImport
    }
    '/mongo': {
      id: '/mongo'
      path: '/mongo'
      fullPath: '/mongo'
      preLoaderRoute: typeof MongoRouteImport
      parentRoute: typeof rootRouteImport
    }
    '/redis': {
      id: '/redis'
      path: '/redis'
      fullPath: '/redis'
      preLoaderRoute: typeof RedisRouteImport
      parentRoute: typeof rootRouteImport
    }
    '/ai-cache': {
      id: '/ai-cache'
      path: '/ai-cache'
      fullPath: '/ai-cache'
      preLoaderRoute: typeof AiCacheRouteImport
      parentRoute: typeof rootRouteImport
    }
    '/performance': {
      id: '/performance'
      path: '/performance'
      fullPath: '/performance'
      preLoaderRoute: typeof PerformanceRouteImport
      parentRoute: typeof rootRouteImport
    }
    '/logs': {
      id: '/logs'
      path: '/logs'
      fullPath: '/logs'
      preLoaderRoute: typeof LogsRouteImport
      parentRoute: typeof rootRouteImport
    }
  }
}
