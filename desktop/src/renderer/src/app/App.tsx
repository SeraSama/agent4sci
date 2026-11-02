import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react'
import { lazy, Suspense, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { BackgroundLayer, usePaneTransparent } from '@/components/Background'
import { ConfirmHost } from '@/components/confirm'
import { Spinner, TooltipProvider } from '@/components/ui'
import { setLanguage } from '@/i18n'
import { get } from '@/lib/api'
import { connectEvents, subscribe } from '@/lib/events'
import { playCue, type Cue } from '@/lib/sound'
import { useUi } from '@/lib/uistore'
import { cn, useToasts } from '@/lib/utils'
import { Terms } from '@/pages/Terms'
import { startThemeSync, useTheme } from '@/theme/store'
import { useRoute } from './router'
import { Sidebar } from './Sidebar'

const pages = {
  chat: lazy(() => import('@/pages/ChatPage')),
  projects: lazy(() => import('@/pages/ProjectsPage')),
  loops: lazy(() => import('@/pages/LoopsPage')),
  runs: lazy(() => import('@/pages/RunsPage')),
  harness: lazy(() => import('@/pages/HarnessPage')),
  skills: lazy(() => import('@/pages/SkillsPage')),
  mcp: lazy(() => import('@/pages/McpPage')),
  memory: lazy(() => import('@/pages/MemoryPage')),
  models: lazy(() => import('@/pages/ModelsPage')),
  settings: lazy(() => import('@/pages/SettingsPage')),
}

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false } },
})

function Toaster() {
  const { toasts, dismiss } = useToasts()
  return (
    <div className="pointer-events-none fixed right-4 bottom-4 z-[60] flex w-80 flex-col gap-2">
      {toasts.map((t) => (
        <div key={t.id} className={cn('pointer-events-auto flex items-start gap-2 rounded-lg border bg-elevated px-3 py-2.5 text-sm shadow-xl',
          t.kind === 'error' ? 'border-danger/40' : t.kind === 'success' ? 'border-success/40' : 'border-border')}>
          {t.kind === 'error' ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger" /> :
            t.kind === 'success' ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" /> :
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-info" />}
          <div className="min-w-0 flex-1 leading-snug break-words">{t.text}</div>
          <button onClick={() => dismiss(t.id)} className="text-subtle hover:text-fg"><X className="h-3.5 w-3.5" /></button>
        </div>
      ))}
    </div>
  )
}

function Shell() {
  const route = useRoute()
  const Page = pages[route.page]
  const { t } = useTranslation()
  const side = useUi((s) => s.ui.sidebarSide)
  const transparent = usePaneTransparent('main')
  const isMac = window.a4s?.platform === 'darwin'
  useEffect(() => {
    connectEvents()
    void useTheme.getState().load()
    void useUi.getState().load()
    get<{ language: string }>('/settings').then((s) => s.language && setLanguage(s.language)).catch(() => undefined)
    // Keep lists fresh when the backend reports changes, and play the matching cue.
    return subscribe('', (e) => {
      if (e.type === 'conversation.status') void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      if (e.type.startsWith('run.')) void queryClient.invalidateQueries({ queryKey: ['runs'] })
      if (e.type === 'catalog.done') void queryClient.invalidateQueries({ queryKey: ['skills-catalog'] })
      const cue = cueFor(e.type, e.data)
      if (cue) playCue(cue)
    })
  }, [])
  return (
    <div className={cn('flex h-screen w-screen overflow-hidden bg-bg text-fg', side === 'right' && 'flex-row-reverse')}>
      <BackgroundLayer region="window" />
      <Sidebar />
      <main className={cn('relative z-10 flex min-w-0 flex-1 flex-col overflow-hidden', !transparent && 'bg-bg')}>
        <BackgroundLayer region="main" />
        {/* The macOS traffic lights stay top-left, so whichever pane is there owns a drag strip. */}
        {isMac && side === 'right' && <div className="h-12 shrink-0" style={{ WebkitAppRegion: 'drag' } as any} />}
        <div className="relative z-10 flex min-h-0 flex-1 flex-col">
          <Suspense fallback={<div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted"><Spinner />{t('加载中…')}</div>}>
            <Page />
          </Suspense>
        </div>
      </main>
      <Toaster />
    </div>
  )
}

/** Which sound a backend event deserves, if any. */
function cueFor(type: string, data: Record<string, unknown> | undefined): Cue | null {
  if (type === 'interaction.request' || type === 'run.waiting') return 'ask'
  if (type === 'agent.error' || type === 'run.failed') return 'error'
  if (type === 'run.started') return 'start'
  if (type === 'run.done' || type === 'agent.done') return 'done'
  if (type === 'conversation.status') return data?.status === 'running' ? 'start' : 'done'
  return null
}

export default function App() {
  useEffect(() => startThemeSync(), [])
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <Shell />
        <ConfirmHost />
        <Terms />
      </TooltipProvider>
    </QueryClientProvider>
  )
}
