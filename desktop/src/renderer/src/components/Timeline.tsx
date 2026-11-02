// Chronological, span-indented view of harness events (LLM turns, tools, sub-agents, skills, memory, nodes).
import { Bot, Brain, ChevronDown, ChevronRight, CircleAlert, Cpu, FileDiff, GitCommitHorizontal, Hand, Layers,
  Lightbulb, Plug, Repeat, Sparkles, Wrench, BookMarked, Archive, Play, CheckCircle2, XCircle, SkipForward } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { BackendEvent } from '@/lib/events'
import { cn, formatCost, formatTokens } from '@/lib/utils'
import { TOOL_LABELS } from './agent'

const SHOWN = new Set([
  'agent.start', 'llm.response', 'llm.retry', 'tool.call', 'tool.result', 'tool.server', 'skill.activated', 'skill.installed',
  'memory.write', 'memory.flagged', 'mcp.call', 'agent.compaction', 'agent.done', 'agent.error', 'interaction.request',
  'interaction.resolved', 'node.started', 'node.done', 'node.failed', 'node.skipped', 'node.note', 'loop.iteration', 'git.commit',
  'finding.recorded', 'reference.added', 'file.changed', 'job.started', 'run.started', 'run.done', 'run.failed',
  'run.waiting', 'run.paused', 'run.cancelled',
])

function describe(e: BackendEvent, t: (s: string, o?: any) => string): { icon: typeof Bot; tone?: string; title: string; meta?: string } {
  const d = e.data
  switch (e.type) {
    case 'agent.start': return { icon: Bot, title: t('Agent 启动：{{n}}', { n: d.name }), meta: d.model_name ?? d.model }
    case 'llm.response': {
      const u = d.usage ?? {}
      const cached = u.cache_read_tokens ?? 0
      return { icon: Cpu, title: d.purpose === 'compaction' ? t('压缩摘要') : t('第 {{n}} 轮 · {{stop}}', { n: d.turn, stop: d.stop }),
        meta: `${t('输入')} ${formatTokens((u.input_tokens ?? 0) + cached + (u.cache_write_tokens ?? 0))}${cached ? `（${t('缓存')} ${formatTokens(cached)}）` : ''} · ${t('输出')} ${formatTokens(u.output_tokens)} · ${formatCost(d.cost)} · ${d.latency_s}s` }
    }
    case 'llm.retry': return { icon: CircleAlert, tone: 'text-warning', title: t('重试（{{k}}）：{{e}}', { k: d.kind, e: String(d.error).slice(0, 80) }) }
    case 'tool.call': return { icon: Wrench, title: t(TOOL_LABELS[d.name] ?? d.name), meta: JSON.stringify(d.input).slice(0, 140) }
    case 'tool.result': return { icon: d.is_error ? CircleAlert : Wrench, tone: d.is_error ? 'text-danger' : 'text-muted',
      title: `${d.is_error ? t('失败') : t('完成')} · ${d.name}`, meta: `${d.duration_s}s · ${String(d.preview ?? '').slice(0, 120)}` }
    case 'tool.server': return { icon: Plug, title: t('服务端工具：{{n}}', { n: d.name }), meta: JSON.stringify(d.input).slice(0, 120) }
    case 'skill.activated': return { icon: Sparkles, tone: 'text-accent', title: t('激活技能：{{n}}', { n: d.name }), meta: d.trust }
    case 'skill.installed': return { icon: Sparkles, tone: 'text-accent', title: t('安装技能：{{s}}', { s: d.source }) }
    case 'memory.write': return { icon: Brain, tone: 'text-info', title: t('记忆 {{c}}', { c: d.command }), meta: d.path }
    case 'memory.flagged': return { icon: CircleAlert, tone: 'text-warning', title: t('记忆内容被标记'), meta: d.reason }
    case 'mcp.call': return { icon: Plug, title: `MCP · ${d.server}.${d.tool}` }
    case 'agent.compaction': return { icon: Archive, tone: 'text-warning', title: t('上下文压缩（{{n}} tokens → 摘要）', { n: formatTokens(d.tokens_before) }) }
    case 'agent.done': return { icon: d.status === 'done' ? CheckCircle2 : CircleAlert, tone: d.status === 'done' ? 'text-success' : 'text-warning',
      title: t('Agent 结束：{{s}}', { s: d.status }), meta: `${d.turns} ${t('轮')} · ${d.tool_calls ?? 0} ${t('次工具')} · ${formatCost(d.cost)}` }
    case 'agent.error': return { icon: XCircle, tone: 'text-danger', title: t('错误'), meta: d.error }
    case 'interaction.request': return { icon: Hand, tone: 'text-warning', title: t('等待人工：{{t}}', { t: d.title }) }
    case 'interaction.resolved': return { icon: Hand, title: t('人工已处理') }
    case 'node.started': return { icon: Play, tone: 'text-primary', title: t('节点开始：{{l}}', { l: d.label || d.node }), meta: d.key }
    case 'node.done': return { icon: CheckCircle2, tone: 'text-success', title: t('节点完成：{{n}}', { n: d.node }), meta: `${formatCost(d.cost)} · ${String(d.preview ?? '').slice(0, 120)}` }
    case 'node.failed': return { icon: XCircle, tone: 'text-danger', title: t('节点失败：{{n}}', { n: d.node }), meta: d.error }
    case 'node.skipped': return { icon: SkipForward, tone: 'text-subtle', title: t('跳过：{{n}}', { n: d.node }) }
    case 'node.note': return { icon: Lightbulb, tone: 'text-warning', title: t('提示：{{n}}', { n: d.node }), meta: t(d.note) }
    case 'loop.iteration': return { icon: Repeat, tone: 'text-primary', title: t('循环 {{n}} 第 {{i}} 轮', { n: d.node, i: d.iteration }),
      meta: d.metric !== null && d.metric !== undefined ? `${d.metric_name} = ${d.metric}${d.improved ? ' ↑' : ''}${d.kept === false ? ` · ${t('已回滚')}` : d.kept ? ` · ${t('已保留')}` : ''}` : undefined }
    case 'git.commit': return { icon: GitCommitHorizontal, title: t('git 提交'), meta: String(d.sha).slice(0, 8) }
    case 'finding.recorded': return { icon: Lightbulb, tone: 'text-accent', title: t('发现 {{id}}', { id: d.id }), meta: d.statement }
    case 'reference.added': return { icon: BookMarked, title: t('参考文献 [@{{k}}]', { k: d.key }), meta: d.title }
    case 'file.changed': return { icon: FileDiff, title: d.action === 'created' ? t('文件创建') : t('文件修改'), meta: d.path }
    case 'job.started': return { icon: Layers, title: t('后台任务 {{id}}', { id: d.job_id }), meta: d.command }
    case 'run.started': return { icon: Play, tone: 'text-primary', title: t('运行开始'), meta: d.loop ? t(d.loop) : undefined }
    case 'run.waiting': return { icon: Hand, tone: 'text-warning', title: t('运行等待人工处理'), meta: d.title }
    case 'run.paused': return { icon: CircleAlert, tone: 'text-warning', title: t('运行已暂停'), meta: d.error }
    case 'run.done': return { icon: CheckCircle2, tone: 'text-success', title: t('运行完成'), meta: d.cost !== undefined ? formatCost(d.cost) : undefined }
    case 'run.failed': return { icon: XCircle, tone: 'text-danger', title: t('运行失败'), meta: d.error }
    case 'run.cancelled': return { icon: XCircle, tone: 'text-subtle', title: t('运行已取消') }
    default: return { icon: Layers, title: e.type }
  }
}

export function Timeline({ events, filter }: { events: BackendEvent[]; filter?: (e: BackendEvent) => boolean }) {
  const { t } = useTranslation()
  const rows = useMemo(() => {
    const parents = new Map<string, string | null>()
    for (const e of events) if (e.data?.span) parents.set(e.data.span, e.data.parent_span ?? null)
    const depth = (span?: string, guard = 0): number => {
      if (!span || guard > 8) return 0
      const p = parents.get(span)
      return p ? 1 + depth(p, guard + 1) : 0
    }
    return events.filter((e) => SHOWN.has(e.type) && (!filter || filter(e))).map((e) => ({ e, depth: depth(e.data?.span) }))
  }, [events, filter])
  if (!rows.length) return <div className="px-4 py-8 text-center text-xs text-subtle">{t('暂无事件')}</div>
  return (
    <div className="flex flex-col py-1 text-xs">
      {rows.map(({ e, depth }) => <Row key={`${e.id}-${e.ts}-${e.type}`} e={e} depth={depth} />)}
    </div>
  )
}

function Row({ e, depth }: { e: BackendEvent; depth: number }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const info = describe(e, t)
  const Icon = info.icon
  const hasDetail = e.type === 'llm.response' ? !!e.data.reasoning : ['tool.call', 'tool.result', 'node.done', 'agent.error', 'node.failed'].includes(e.type)
  return (
    <div className="border-b border-border/50" style={{ paddingLeft: 10 + depth * 18 }}>
      <button className="flex w-full items-start gap-2 py-1.5 pr-3 text-left hover:bg-surface-2/60" onClick={() => hasDetail && setOpen(!open)}>
        {hasDetail ? (open ? <ChevronDown className="mt-0.5 h-3 w-3 shrink-0 text-subtle" /> : <ChevronRight className="mt-0.5 h-3 w-3 shrink-0 text-subtle" />) : <span className="w-3 shrink-0" />}
        <Icon className={cn('mt-0.5 h-3.5 w-3.5 shrink-0', info.tone ?? 'text-muted')} />
        <span className="shrink-0 font-medium">{info.title}</span>
        {info.meta && <span className="min-w-0 flex-1 truncate text-muted">{info.meta}</span>}
        <span className="ml-auto shrink-0 font-mono text-[10px] text-subtle">{new Date(e.ts * 1000).toLocaleTimeString()}</span>
      </button>
      {open && (
        <pre className="mb-2 mr-3 max-h-64 overflow-auto rounded bg-code p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
          {e.type === 'llm.response' ? e.data.reasoning : JSON.stringify(e.data, null, 2)}
        </pre>
      )}
    </div>
  )
}
