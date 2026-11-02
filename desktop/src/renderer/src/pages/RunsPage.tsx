import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, AlertTriangle, Pause, Play, ShieldCheck, Square, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts'
import { navigate, useRoute } from '@/app/router'
import { InteractionCard } from '@/components/agent'
import { confirmAction } from '@/components/confirm'
import { LoopCanvas, type NodeStatus } from '@/components/graph/LoopCanvas'
import { Markdown } from '@/components/Markdown'
import { Timeline } from '@/components/Timeline'
import { Badge, Button, Empty, Spinner, TabPanel, Tabs, Tip } from '@/components/ui'
import { del, get, post } from '@/lib/api'
import { useEvents, type BackendEvent } from '@/lib/events'
import { useCurrentProject } from '@/lib/store'
import type { RunDetail, RunSummary } from '@/lib/types'
import { cn, costLabel, formatCost, formatTime, formatTokens } from '@/lib/utils'

export const RUN_STATUS: Record<string, { label: string; tone: 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info' }> = {
  pending: { label: '等待开始', tone: 'neutral' }, running: { label: '运行中', tone: 'info' }, waiting: { label: '等待人工', tone: 'warning' },
  paused: { label: '已暂停', tone: 'neutral' }, done: { label: '完成', tone: 'success' }, failed: { label: '失败', tone: 'danger' },
  cancelled: { label: '已取消', tone: 'neutral' },
}

export default function RunsPage() {
  const { t } = useTranslation()
  const route = useRoute()
  const project = useCurrentProject()
  const [all, setAll] = useState(!project)
  const { data: runs } = useQuery({
    queryKey: ['runs', all ? null : project?.id],
    queryFn: () => get<RunSummary[]>('/runs', { project_id: all ? undefined : project?.id }),
    refetchInterval: 5000,
  })
  const selected = route.param ?? runs?.[0]?.id
  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex w-[260px] shrink-0 flex-col border-r border-border bg-surface/60">
        <div className="flex items-center justify-between px-3 pt-4 pb-2">
          <span className="text-sm font-semibold">{t('运行')}</span>
          {project && <button className="text-[11px] text-muted hover:text-fg" onClick={() => setAll(!all)}>{all ? t('只看当前项目') : t('显示全部')}</button>}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {runs?.map((r) => (
            <button key={r.id} onClick={() => navigate('runs', r.id)}
              className={cn('flex w-full flex-col gap-1 rounded-md px-2.5 py-2 text-left transition', r.id === selected ? 'bg-primary/12' : 'hover:bg-surface-2')}>
              <span className="flex w-full items-center gap-2"><span className="min-w-0 flex-1 truncate text-sm">{t(r.loop_name)}</span>
                {!!r.warnings?.length && <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warning" />}
                <Badge tone={RUN_STATUS[r.status]?.tone}>{t(RUN_STATUS[r.status]?.label ?? r.status)}</Badge></span>
              <span className="text-[11px] text-subtle">{formatTime(r.created)} · {costLabel(r.cost_usd, r.cost_unknown)}{r.auto_mode ? ` · ${t('全自动')}` : ''}</span>
            </button>
          ))}
          {!runs?.length && <div className="px-3 py-8 text-center text-xs text-subtle">{t('还没有运行记录')}</div>}
        </div>
      </div>
      {selected ? <RunView key={selected} id={selected} /> : <div className="flex-1"><Empty icon={<Activity className="h-10 w-10" />}
        title={t('还没有运行')} hint={t('在 Loop 设计器里选择一个模板并点击“运行”。')} action={<Button onClick={() => navigate('loops')}>{t('打开 Loop 设计器')}</Button>} /></div>}
    </div>
  )
}

function RunView({ id }: { id: string }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { data: run } = useQuery({ queryKey: ['run', id], queryFn: () => get<RunDetail>(`/runs/${id}`) })
  const [events, setEvents] = useState<BackendEvent[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [tab, setTab] = useState('node')
  const timer = useRef<number | null>(null)

  useEffect(() => {
    get<BackendEvent[]>('/events', { topic: `run:${id}` }).then(setEvents).catch(() => undefined)
  }, [id])
  useEvents(`run:${id}`, (e) => {
    if (e.type.endsWith('.delta')) return
    setEvents((ev) => [...ev, e])
    if (/^(node\.|loop\.|run\.|interaction\.)/.test(e.type)) {
      if (timer.current) window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => void qc.invalidateQueries({ queryKey: ['run', id] }), 250)
    }
  })

  const statuses = useMemo(() => {
    const out: Record<string, NodeStatus> = {}
    if (!run) return out
    for (const [nodeId, s] of Object.entries(run.nodes)) out[nodeId] = { status: s.status, error: s.error }
    for (const key of Object.keys(run.records)) {
      const m = key.match(/^(?:.*\/)?([^/#[\]<>]+)#(\d+)$/)
      if (m) out[m[1]] = { ...(out[m[1]] ?? { status: 'running' }), iterations: Math.max(out[m[1]]?.iterations ?? 0, Number(m[2])) }
    }
    if (run.waiting?.node) out[run.waiting.node] = { ...(out[run.waiting.node] ?? {}), status: 'waiting' }
    return out
  }, [run])

  if (!run) return <div className="flex flex-1 items-center justify-center"><Spinner /></div>
  const st = RUN_STATUS[run.status]
  const active = ['running', 'waiting', 'pending'].includes(run.status)
  const nodeRecords = Object.values(run.records).filter((r) => r.node === selected && !r.key.match(/#\d+$/))
  const iterRecords = Object.values(run.records).filter((r) => r.node === selected && r.key.match(/#\d+$/))

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="flex items-center gap-3 border-b border-border px-5 py-2.5">
        <span className="truncate text-sm font-semibold">{t(run.loop_name)}</span>
        <Badge tone={st?.tone}>{t(st?.label ?? run.status)}</Badge>
        {run.auto_mode && <Badge>{t('全自动')}</Badge>}
        <span className="text-xs text-muted tabular-nums" title={run.cost_unknown ? t('所用模型没有设置价格，无法统计费用（在“模型”页填写单价）') : undefined}>
          {costLabel(run.cost_usd, run.cost_unknown)}</span>
        <span className="min-w-0 flex-1 truncate text-xs text-danger">{run.error}</span>
        {active && <Button size="sm" variant="outline" onClick={() => post(`/runs/${id}/pause`)}><Pause className="h-3.5 w-3.5" />{t('暂停')}</Button>}
        {['paused', 'failed', 'cancelled'].includes(run.status) && (
          <Button size="sm" variant="primary" onClick={() => post(`/runs/${id}/resume`).then(() => qc.invalidateQueries({ queryKey: ['run', id] }))}>
            <Play className="h-3.5 w-3.5" />{t('继续运行')}</Button>
        )}
        {active && <Button size="sm" variant="outline" onClick={async () => {
          if (await confirmAction({ title: t('取消这次运行？'), danger: true, confirmLabel: t('取消运行') })) await post(`/runs/${id}/cancel`)
        }}><Square className="h-3.5 w-3.5" />{t('取消')}</Button>}
        {!active && run.project_id && (
          <Tip content={t('检查这次运行的产物：引用、数字出处、发现账本的证据')}>
            <Button size="sm" variant="outline" onClick={() => navigate('projects', run.project_id, { tab: 'audit' })}>
              <ShieldCheck className="h-3.5 w-3.5" />{t('自检产物')}</Button>
          </Tip>
        )}
        {!active && <Button size="icon" variant="ghost" className="text-danger" onClick={async () => {
          if (!(await confirmAction({ title: t('删除这条运行记录？'), description: t('工作区文件不受影响。'), danger: true }))) return
          await del(`/runs/${id}`)
          await qc.invalidateQueries({ queryKey: ['runs'] })
          navigate('runs')
        }}><Trash2 className="h-3.5 w-3.5" /></Button>}
      </div>
      {!!run.warnings?.length && (
        <div className="flex flex-col gap-1 border-b border-warning/30 bg-warning/8 px-5 py-2 text-xs">
          {run.warnings.map((w) => (
            <div key={w} className="flex items-start gap-2"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />{w}</div>
          ))}
        </div>
      )}
      {run.pending.length > 0 && (
        <div className="flex flex-col gap-2 border-b border-border bg-surface/60 px-5 py-3">
          {run.pending.map((item) => <InteractionCard key={item.id} item={item} onDone={() => void qc.invalidateQueries({ queryKey: ['run', id] })} />)}
        </div>
      )}
      <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <div className="relative border-b border-border">
          <LoopCanvas graph={run.graph} statuses={statuses} onSelect={(s) => { if (s?.kind === 'node') { setSelected(s.id); setTab('node') } }} />
        </div>
        <Tabs value={tab} onValueChange={setTab} tabs={[
          { value: 'node', label: selected ? t('节点：{{n}}', { n: selected }) : t('节点详情') },
          { value: 'cost', label: t('花在哪') }, { value: 'timeline', label: t('时间线') },
          { value: 'metrics', label: t('指标') }, { value: 'inputs', label: t('输入') },
        ]}>
          <TabPanel value="node" className="px-5 py-3">
            {!selected ? <div className="py-6 text-center text-xs text-subtle">{t('点击上方图中的节点查看输出')}</div> : (
              <div className="flex flex-col gap-3">
                {iterRecords.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {iterRecords.map((r) => (
                      <div key={r.key} className="rounded-md border border-border px-3 py-2 text-xs">
                        <div className="font-medium">{t('第 {{n}} 轮', { n: r.output?.iteration })}</div>
                        {r.output?.metric !== null && r.output?.metric !== undefined && <div className="text-muted">{t('指标')} {r.output.metric}{r.output.kept === false ? ` · ${t('已回滚')}` : r.output.kept ? ` · ${t('已保留')}` : ''}</div>}
                      </div>
                    ))}
                  </div>
                )}
                {nodeRecords.map((r) => (
                  <div key={r.key} className="rounded-md border border-border p-3">
                    <div className="mb-2 flex items-center gap-2 text-xs">
                      <Badge tone={r.status === 'done' ? 'success' : r.status === 'failed' ? 'danger' : 'info'}>{r.status}</Badge>
                      <span className="font-mono text-subtle">{r.key}</span>
                      <span className="ml-auto text-muted">{formatCost(r.cost)}{r.finished ? ` · ${(r.finished - r.started).toFixed(1)}s` : ''}</span>
                    </div>
                    {r.error && <div className="mb-2 text-xs text-danger">{r.error}</div>}
                    {r.output?.summary && <Markdown className="text-sm" text={String(r.output.summary)} />}
                    {r.output && (
                      <details className="mt-2 text-xs">
                        <summary className="cursor-pointer text-muted">{t('完整输出')}</summary>
                        <pre className="mt-1 max-h-80 overflow-auto rounded bg-code p-2 font-mono text-[11px] whitespace-pre-wrap">{JSON.stringify(r.output, null, 2)}</pre>
                      </details>
                    )}
                  </div>
                ))}
                {!nodeRecords.length && !iterRecords.length && <div className="text-xs text-subtle">{t('这个节点还没有运行')}</div>}
              </div>
            )}
          </TabPanel>
          <TabPanel value="cost" className="px-5 py-3"><CostBreakdown run={run} /></TabPanel>
          <TabPanel value="timeline"><Timeline events={events} filter={selected ? (e) => e.data?.node === selected || !e.data?.node : undefined} /></TabPanel>
          <TabPanel value="metrics" className="px-5 py-3"><Metrics run={run} /></TabPanel>
          <TabPanel value="inputs" className="px-5 py-3">
            <pre className="rounded bg-code p-3 font-mono text-xs whitespace-pre-wrap">{JSON.stringify(run.inputs, null, 2)}</pre>
          </TabPanel>
        </Tabs>
      </div>
    </div>
  )
}

function Metrics({ run }: { run: RunDetail }) {
  const { t } = useTranslation()
  const names = Object.keys(run.metrics)
  if (!names.length) return <div className="py-6 text-center text-xs text-subtle">{t('这个运行没有定义指标（在循环节点里设置指标文件与 JSON 指针）')}</div>
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      {names.map((name, i) => {
        const data = run.metrics[name].map((m) => ({ ...m, iteration: m.iteration }))
        const color = `var(--a4s-chart-${(i % 6) + 1})`
        return (
          <div key={name} className="rounded-md border border-border p-3">
            <div className="mb-2 text-xs font-medium">{name}</div>
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={data} margin={{ top: 5, right: 12, bottom: 0, left: -12 }}>
                  <CartesianGrid stroke="var(--a4s-border)" strokeDasharray="3 3" />
                  <XAxis dataKey="iteration" stroke="var(--a4s-fg-subtle)" fontSize={11} />
                  <YAxis stroke="var(--a4s-fg-subtle)" fontSize={11} />
                  <ChartTooltip contentStyle={{ background: 'var(--a4s-elevated)', border: '1px solid var(--a4s-border)', borderRadius: 6, fontSize: 12 }} />
                  <Line type="monotone" dataKey="value" stroke={color} strokeWidth={2} isAnimationActive={false}
                    dot={(props: any) => <circle key={props.index} cx={props.cx} cy={props.cy} r={4}
                      fill={props.payload.kept === false ? 'var(--a4s-danger)' : props.payload.improved ? 'var(--a4s-success)' : color} />} />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-1 flex gap-3 text-[11px] text-muted">
              <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-success" />{t('改进并保留')}</span>
              <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-danger" />{t('未改进已回滚')}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** Per-node cost and tokens, so "why was this run expensive" has an answer without clicking every node. */
function CostBreakdown({ run }: { run: RunDetail }) {
  const { t } = useTranslation()
  const rows = useMemo(() => {
    const byNode = new Map<string, { cost: number; input: number; output: number; runs: number }>()
    for (const rec of Object.values(run.records ?? {})) {
      if (rec.key.includes('#')) continue  // iterations roll up into their loop node
      const row = byNode.get(rec.node) ?? { cost: 0, input: 0, output: 0, runs: 0 }
      row.cost += rec.cost ?? 0
      row.input += rec.tokens?.input ?? 0
      row.output += rec.tokens?.output ?? 0
      row.runs += 1
      byNode.set(rec.node, row)
    }
    return [...byNode.entries()].map(([node, v]) => ({ node, ...v })).sort((a, b) =>
      (b.cost - a.cost) || (b.input + b.output - a.input - a.output))
  }, [run.records])
  const total = rows.reduce((n, r) => n + r.cost, 0)
  const label = (id: string) => run.graph.nodes.find((n) => n.id === id)?.label || id

  if (!rows.length) return <div className="py-6 text-center text-xs text-subtle">{t('还没有花费记录')}</div>
  return (
    <div className="flex max-w-3xl flex-col gap-2">
      <div className="flex items-center gap-2 text-xs text-muted">
        <span>{t('合计')} {costLabel(run.cost_usd, run.cost_unknown)}</span>
        {run.tokens?.input !== undefined && (
          <span>· {t('输入 {{i}} · 输出 {{o}} tokens', { i: formatTokens(run.tokens.input), o: formatTokens(run.tokens.output ?? 0) })}</span>
        )}
        {run.tokens?.cached ? <span>· {t('其中缓存命中 {{n}}', { n: formatTokens(run.tokens.cached) })}</span> : null}
      </div>
      <table className="w-full text-xs">
        <thead className="text-subtle">
          <tr className="border-b border-border">
            <th className="py-1 text-left font-medium">{t('节点')}</th>
            <th className="py-1 text-right font-medium">{t('花费')}</th>
            <th className="py-1 text-right font-medium">{t('输入')}</th>
            <th className="py-1 text-right font-medium">{t('输出')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.node} className="border-b border-border/50">
              <td className="py-1.5">
                {t(label(r.node))}
                {r.runs > 1 && <span className="ml-1 text-subtle">×{r.runs}</span>}
                {total > 0 && (
                  <div className="mt-0.5 h-1 w-32 overflow-hidden rounded-full bg-surface-2">
                    <div className="h-full rounded-full bg-primary" style={{ width: `${Math.round((r.cost / total) * 100)}%` }} />
                  </div>
                )}
              </td>
              <td className="py-1.5 text-right tabular-nums">{run.cost_unknown && !r.cost ? '—' : formatCost(r.cost)}</td>
              <td className="py-1.5 text-right tabular-nums text-muted">{formatTokens(r.input)}</td>
              <td className="py-1.5 text-right tabular-nums text-muted">{formatTokens(r.output)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
