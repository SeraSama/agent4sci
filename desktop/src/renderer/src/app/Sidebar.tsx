import { Activity, Bot, Brain, FolderKanban, Layers, MessageSquare, PanelLeftClose, PanelLeftOpen,
  PanelRightClose, PanelRightOpen, Plug, Settings, Sparkles, Workflow } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BackgroundLayer, usePaneTransparent } from '@/components/Background'
import { Logo } from '@/components/Logo'
import { Tip } from '@/components/ui'
import { useConnection } from '@/lib/events'
import { useApp, useInfo, useProjects } from '@/lib/store'
import { useUi } from '@/lib/uistore'
import { cn } from '@/lib/utils'
import { navigate, useRoute, type PageId } from './router'

const NAV: { id: PageId; label: string; icon: typeof MessageSquare; group: number }[] = [
  { id: 'chat', label: '对话', icon: MessageSquare, group: 0 },
  { id: 'projects', label: '项目', icon: FolderKanban, group: 0 },
  { id: 'loops', label: 'Loop 设计器', icon: Workflow, group: 1 },
  { id: 'runs', label: '运行监控', icon: Activity, group: 1 },
  { id: 'harness', label: 'Harness', icon: Layers, group: 1 },
  { id: 'skills', label: '技能', icon: Sparkles, group: 2 },
  { id: 'mcp', label: 'MCP', icon: Plug, group: 2 },
  { id: 'memory', label: '记忆', icon: Brain, group: 2 },
  { id: 'models', label: '模型', icon: Bot, group: 3 },
  { id: 'settings', label: '设置', icon: Settings, group: 3 },
]

export function Sidebar() {
  const { t } = useTranslation()
  const route = useRoute()
  const { projectId, setProject, sidebarCollapsed, toggleSidebar } = useApp()
  const { data: projects } = useProjects()
  const { data: info } = useInfo()
  const status = useConnection((s) => s.status)
  const side = useUi((s) => s.ui.sidebarSide)
  const transparent = usePaneTransparent('sidebar')
  const isMac = window.a4s?.platform === 'darwin'
  const collapsed = sidebarCollapsed
  const [played, setPlayed] = useState<Record<string, number>>({})
  // Only the pane at the window's left edge has to make room for the macOS traffic lights.
  const inset = isMac && side === 'left'
  const [Close, Open] = side === 'left' ? [PanelLeftClose, PanelLeftOpen] : [PanelRightClose, PanelRightOpen]

  return (
    <aside className={cn('relative z-10 flex shrink-0 flex-col border-border transition-[width] duration-150',
      side === 'left' ? 'border-r' : 'border-l', transparent ? 'bg-transparent' : 'bg-surface',
      collapsed ? 'w-[60px]' : 'w-[216px]')}>
      <BackgroundLayer region="sidebar" />
      <div className={cn('relative z-10 flex h-12 items-center gap-2 px-3', inset && 'pl-[78px]')} style={{ WebkitAppRegion: 'drag' } as any}>
        <div className="flex min-w-0 items-center gap-2">
          <Logo className="h-5 w-5 shrink-0 rounded-[5px]" />
          {!collapsed && <span className="truncate text-[13px] font-semibold tracking-[0.01em]">Agent4Sci</span>}
        </div>
      </div>

      {!collapsed && (
        <div className="relative z-10 px-3 pb-2">
          <select
            value={projectId ?? ''}
            onChange={(e) => setProject(e.target.value || null)}
            className="h-8 w-full cursor-pointer truncate rounded-md border border-border bg-surface-2 px-2 text-xs text-fg outline-none focus:border-primary"
            title={t('当前项目')}
          >
            <option value="">{t('（未选择项目）')}</option>
            {projects?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      )}

      <nav className="relative z-10 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2 py-1">
        {NAV.map((item, i) => {
          const Icon = item.icon
          const active = route.page === item.id
          const button = (
            <button
              key={item.id}
              onClick={() => {
                // Re-keying the icon restarts its draw animation, so a second click replays it.
                setPlayed((n) => ({ ...n, [item.id]: (n[item.id] ?? 0) + 1 }))
                navigate(item.id)
              }}
              className={cn('group flex h-8.5 items-center gap-2.5 rounded-md px-2.5 text-sm transition',
                active ? 'bg-primary/12 font-medium text-primary' : 'text-muted hover:bg-surface-2 hover:text-fg',
                collapsed && 'justify-center px-0')}
            >
              <Icon key={played[item.id] ?? 0} className={cn('h-4 w-4 shrink-0', played[item.id] && 'a4s-draw')} />
              {!collapsed && <span className="truncate">{t(item.label)}</span>}
            </button>
          )
          const separator = i > 0 && NAV[i - 1].group !== item.group
          return (
            <div key={item.id}>
              {separator && <div className="mx-2 my-1.5 h-px bg-border" />}
              {collapsed ? <Tip content={t(item.label)} side="right">{button}</Tip> : button}
            </div>
          )
        })}
      </nav>

      <div className={cn('relative z-10 flex items-center gap-2 border-t border-border px-3 py-2.5 text-[11px] text-muted', collapsed && 'flex-col')}>
        <Tip content={status === 'open' ? t('已连接后端') : status === 'connecting' ? t('正在连接后端…') : t('后端连接已断开')}>
          <span className={cn('h-2 w-2 shrink-0 rounded-full',
            status === 'open' ? 'bg-success' : status === 'connecting' ? 'bg-warning animate-pulse' : 'bg-danger')} />
        </Tip>
        {!collapsed && (
          <span className="min-w-0 flex-1 truncate">
            {info?.demo ? t('演示模式') : `v${info?.version ?? ''}`}
          </span>
        )}
        <button onClick={toggleSidebar} className="rounded p-1 hover:bg-surface-2 hover:text-fg"
          title={collapsed ? t('展开侧栏') : t('折叠侧栏')}>
          {collapsed ? <Open className="h-3.5 w-3.5" /> : <Close className="h-3.5 w-3.5" />}
        </button>
      </div>
    </aside>
  )
}
