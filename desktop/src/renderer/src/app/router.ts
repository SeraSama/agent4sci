// Tiny hash router: #/page/param?x=y — survives reloads and works in Electron file:// pages.
import { useSyncExternalStore } from 'react'

export type PageId = 'chat' | 'projects' | 'loops' | 'runs' | 'harness' | 'skills' | 'mcp' | 'memory' | 'models' | 'settings'

export interface Route {
  page: PageId
  param?: string
  query: URLSearchParams
}

function parse(): Route {
  const hash = window.location.hash.replace(/^#\/?/, '')
  const [path, qs = ''] = hash.split('?')
  const [page, ...rest] = path.split('/')
  const pages: PageId[] = ['chat', 'projects', 'loops', 'runs', 'harness', 'skills', 'mcp', 'memory', 'models', 'settings']
  return {
    page: (pages.includes(page as PageId) ? page : 'chat') as PageId,
    param: rest.length ? decodeURIComponent(rest.join('/')) : undefined,
    query: new URLSearchParams(qs),
  }
}

let current = parse()
const listeners = new Set<() => void>()
window.addEventListener('hashchange', () => {
  current = parse()
  listeners.forEach((l) => l())
})

export function useRoute(): Route {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    () => current,
  )
}

export function navigate(page: PageId, param?: string, query?: Record<string, string>): void {
  const qs = query ? '?' + new URLSearchParams(query).toString() : ''
  window.location.hash = `/${page}${param ? '/' + encodeURIComponent(param) : ''}${qs}`
}
