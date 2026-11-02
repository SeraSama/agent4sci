import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, BrainCircuit, ChevronDown, ChevronRight, Ghost, Globe, Layers, MessageSquare, MessagesSquare, Plus,
  Pencil, Save, Search, Send, ShieldCheck, Sparkles, Square, Trash2, UserRound, FolderKanban, BookmarkPlus,
  Archive, BookMarked, FlaskConical, Puzzle, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { navigate, useRoute } from '@/app/router'
import { InteractionCard, TOOL_LABELS, ToolCallCard, type ToolCallView } from '@/components/agent'
import { FileChip } from '@/components/FileChip'
import { Markdown } from '@/components/Markdown'
import { Marquee } from '@/components/Marquee'
import { askForText, confirmAction } from '@/components/confirm'
import { useContextMenu } from '@/components/ContextMenu'
import { Badge, Button, Dialog, Empty, Input, Select, Spinner, Tip } from '@/components/ui'
import { del, errorMessage, get, patch, post, put } from '@/lib/api'
import { useEvents } from '@/lib/events'
import { useApp, useCurrentProject, useModels, useProjects } from '@/lib/store'
import type { Block, ChatMessage, Conversation, HarnessProfile, Interaction, MemoryMode } from '@/lib/types'
import { cn, costLabel, formatCost, formatTime, formatTokens, toast } from '@/lib/utils'

export const MEMORY_MODES: { value: MemoryMode; label: string; icon: typeof Globe; hint: string }[] = [
  { value: 'global', label: '全局记忆', icon: Globe, hint: '读写全局、项目和本对话的记忆' },
  { value: 'project', label: '项目记忆', icon: FolderKanban, hint: '只用本项目与本对话的记忆' },
  { value: 'conversation', label: '仅本对话', icon: MessageSquare, hint: '记忆只在这个对话（及其子代理、发起的运行）内有效' },
  { value: 'none', label: '无记忆', icon: Ghost, hint: '不读取也不写入任何记忆' },
]

export default function ChatPage() {
  const route = useRoute()
  return (
    <div className="flex min-h-0 flex-1">
      <ConversationList activeId={route.param} />
      {route.param ? <ChatView key={route.param} id={route.param} /> : <Welcome />}
    </div>
  )
}

function useConversations() {
  return useQuery({ queryKey: ['conversations'], queryFn: () => get<Conversation[]>('/conversations') })
}

async function createConversation(body: Record<string, any>): Promise<Conversation> {
  return post<Conversation>('/conversations', body)
}

function ConversationList({ activeId }: { activeId?: string }) {
  const { t } = useTranslation()
  const { data } = useConversations()
  const project = useCurrentProject()
  const { data: projects } = useProjects()
  const qc = useQueryClient()
  const [q, setQ] = useState('')
  const filtered = (data ?? []).filter((c) => !q || c.title.toLowerCase().includes(q.toLowerCase()))
  const groups = useMemo(() => {
    const byProject = new Map<string, Conversation[]>()
    for (const c of filtered) {
      const key = c.temporary ? '__temp' : c.project_id ?? '__none'
      byProject.set(key, [...(byProject.get(key) ?? []), c])
    }
    const order = ['__temp', ...(project ? [project.id] : []), ...[...byProject.keys()].filter((k) => !k.startsWith('__') && k !== project?.id), '__none']
    return order.filter((k) => byProject.has(k)).map((k) => ({
      key: k, items: byProject.get(k)!,
      label: k === '__temp' ? t('临时对话') : k === '__none' ? t('未归属项目') : projects?.find((p) => p.id === k)?.name ?? t('项目'),
    }))
  }, [filtered, project, projects, t])

  const start = async (temporary: boolean) => {
    try {
      const conv = await createConversation({ project_id: temporary ? null : project?.id ?? null, temporary })
      await qc.invalidateQueries({ queryKey: ['conversations'] })
      navigate('chat', conv.id)
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }

  return (
    <div className="flex w-[264px] shrink-0 flex-col border-r border-border bg-surface/60">
      <div className="flex items-center gap-2 px-3 pt-4 pb-2">
        <Button variant="primary" size="sm" className="flex-1" onClick={() => start(false)}><Plus className="h-3.5 w-3.5" />{t('新对话')}</Button>
        <Tip content={t('临时对话：不保存、不读写记忆，关闭即丢弃')}>
          <Button size="sm" variant="outline" onClick={() => start(true)}><Ghost className="h-3.5 w-3.5" />{t('临时')}</Button>
        </Tip>
      </div>
      <div className="px-3 pb-2">
        <div className="relative">
          <Search className="absolute top-2 left-2 h-3.5 w-3.5 text-subtle" />
          <Input className="h-7.5 pl-7 text-xs" placeholder={t('搜索对话')} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {groups.map((g) => (
          <div key={g.key} className="mt-2">
            <div className="px-2 pb-1 text-[11px] font-medium text-subtle">{g.label}</div>
            {g.items.map((c) => <ConversationRow key={c.id} conv={c} active={c.id === activeId} />)}
          </div>
        ))}
        {!data?.length && <div className="px-3 py-8 text-center text-xs text-subtle">{t('还没有对话')}</div>}
      </div>
    </div>
  )
}

/** One conversation in the list; right-click for rename / archive / delete. */
function ConversationRow({ conv, active }: { conv: Conversation; active: boolean }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const mode = MEMORY_MODES.find((m) => m.value === conv.memory_mode) ?? MEMORY_MODES[0]
  const Icon = conv.temporary ? Ghost : mode.icon
  const refresh = () => qc.invalidateQueries({ queryKey: ['conversations'] })

  const rename = async () => {
    const title = await askForText({ title: t('重命名对话'), input: { label: t('标题'), initial: conv.title, required: true } })
    if (!title) return
    await patch(`/conversations/${conv.id}`, { title })
    await refresh()
  }

  const remove = async () => {
    if (!(await confirmAction({ title: t('删除对话「{{n}}」？', { n: conv.title }),
      description: t('它的对话级记忆也会一起删除。'), danger: true }))) return
    await del(`/conversations/${conv.id}`)
    await refresh()
    if (active) navigate('chat')
  }

  const menu = useContextMenu([
    { label: t('重命名'), icon: <Pencil className="h-3.5 w-3.5" />, onSelect: () => void rename() },
    { label: t('归档'), icon: <Archive className="h-3.5 w-3.5" />, disabled: conv.temporary, onSelect: async () => {
      await patch(`/conversations/${conv.id}`, { archived: true })
      await refresh()
      if (active) navigate('chat')
    } },
    { label: t('删除'), icon: <Trash2 className="h-3.5 w-3.5" />, danger: true, separatorBefore: true,
      onSelect: () => void remove() },
  ])

  return (
    <>
      <button {...menu.trigger} onClick={() => navigate('chat', conv.id)}
        className={cn('group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition',
          active ? 'bg-primary/12 text-fg' : 'text-muted hover:bg-surface-2 hover:text-fg')}>
        <Icon className={cn('h-3.5 w-3.5 shrink-0', conv.temporary && 'text-warning')} />
        <span className="min-w-0 flex-1 truncate">{conv.title}</span>
        {conv.status === 'running' ? <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-primary" />
          : <span className="shrink-0 text-[10px] text-subtle">{formatTime(conv.updated)}</span>}
      </button>
      {menu.menu}
    </>
  )
}

function Welcome() {
  const { t } = useTranslation()
  const project = useCurrentProject()
  const { data: models } = useModels()
  const qc = useQueryClient()
  const noModel = (models?.models.length ?? 0) === 0
  const start = async (body: Record<string, any>) => {
    const conv = await createConversation(body)
    await qc.invalidateQueries({ queryKey: ['conversations'] })
    navigate('chat', conv.id)
  }
  return (
    <div className="flex flex-1 items-center justify-center overflow-y-auto p-8">
      <div className="w-full max-w-2xl">
        <div className="mb-6 flex items-center gap-3">
          <div className="rounded-xl bg-primary/12 p-3 text-primary"><BrainCircuit className="h-7 w-7" /></div>
          <div>
            <h1 className="text-xl font-semibold">{t('开始一个研究对话')}</h1>
            <p className="text-sm text-muted">{project ? t('当前项目：{{name}}', { name: project.name }) : t('未选择项目：对话在独立的临时工作区中进行')}</p>
          </div>
        </div>
        {noModel && (
          <div className="mb-5 rounded-lg border border-warning/40 bg-warning/8 p-3 text-sm">
            {t('还没有可用的模型。')}
            <button className="ml-1 text-primary underline" onClick={() => navigate('models')}>{t('去添加模型')}</button>
            <span className="mx-1 text-muted">{t('或')}</span>
            <button className="text-primary underline" onClick={async () => {
              try {
                await put('/settings', { demo_mode: true })
                await Promise.all(['models', 'settings', 'info'].map((k) => qc.invalidateQueries({ queryKey: [k] })))
                toast.success(t('已启用演示模型：无需 API Key，可随时在“设置 → 通用”中关闭'))
              } catch (err) {
                toast.error(errorMessage(err))
              }
            }}>{t('启用演示模型')}</button>
          </div>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          {MEMORY_MODES.filter((m) => m.value !== 'none' && (m.value !== 'project' || project)).map((m) => (
            <button key={m.value} onClick={() => start({ project_id: project?.id ?? null, memory_mode: m.value })}
              className="flex items-start gap-3 rounded-lg border border-border bg-surface p-4 text-left transition hover:border-primary hover:bg-primary/5">
              <m.icon className="mt-0.5 h-4.5 w-4.5 text-primary" />
              <div><div className="text-sm font-medium">{t('新对话 · {{mode}}', { mode: t(m.label) })}</div>
                <div className="mt-0.5 text-xs text-muted">{t(m.hint)}</div></div>
            </button>
          ))}
          <button onClick={() => start({ temporary: true })}
            className="flex items-start gap-3 rounded-lg border border-dashed border-warning/50 bg-surface p-4 text-left transition hover:bg-warning/5">
            <Ghost className="mt-0.5 h-4.5 w-4.5 text-warning" />
            <div><div className="text-sm font-medium">{t('临时对话')}</div>
              <div className="mt-0.5 text-xs text-muted">{t('不保存、不读写记忆，关闭即丢弃；需要时可转为正式对话')}</div></div>
          </button>
        </div>
        <div className="mt-6 rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted">
          {t('想要一个新的 Agent 或自动化流程？在对话顶部把 Harness 切到「主理人」，告诉它你要做什么，它会帮你写好 Harness 与 Loop 并保存。')}
        </div>
        <div className="mt-3 grid gap-2 text-xs text-muted sm:grid-cols-3">
          {[[BookMarked, '检索并阅读文献，生成经过核验的参考文献'],
            [FlaskConical, '在工作区里写代码、跑实验、画图'],
            [Puzzle, '按需激活技能、调用 MCP 工具']].map(([Icon, label]: any) => (
            <div key={label} className="flex items-start gap-2 rounded-md bg-surface-2 p-3">
              <Icon className="mt-px h-3.5 w-3.5 shrink-0 text-subtle" />
              <span>{t(label)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

interface Live {
  text: string
  reasoning: string
}

function ChatView({ id }: { id: string }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { data, error } = useQuery({
    queryKey: ['conversation', id],
    queryFn: () => get<Conversation & { messages: ChatMessage[]; pending: Interaction[] }>(`/conversations/${id}`),
  })
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [live, setLive] = useState<Live>({ text: '', reasoning: '' })
  const [running, setRunning] = useState<Record<string, boolean>>({})
  const [results, setResults] = useState<Record<string, { preview: string; is_error: boolean; duration: number }>>({})
  const [pending, setPending] = useState<Interaction[]>([])
  const [status, setStatus] = useState<'idle' | 'running'>('idle')
  const [usage, setUsage] = useState<{ context: number; window: number } | null>(null)
  const [notes, setNotes] = useState<string[]>([])
  const [activity, setActivity] = useState<{ subs: number; label: string }>({ subs: 0, label: '' })
  const [quiet, setQuiet] = useState(0)
  const bottom = useRef<HTMLDivElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  const lastEvent = useRef(Date.now())

  useEffect(() => {
    if (!data) return
    setMessages(data.messages)
    setPending(data.pending)
    setStatus(data.status)
  }, [data])

  useEvents(`conv:${id}`, (e) => {
    const top = e.data.parent_span == null
    lastEvent.current = Date.now()
    setQuiet(0)
    const toolLabel = (name: string) => (TOOL_LABELS[name] ? t(TOOL_LABELS[name]) : name)
    switch (e.type) {
      case 'agent.start':
        if (!top) setActivity((a) => ({ subs: a.subs + 1, label: t('子代理 {{role}} 开始工作', { role: e.data.role ?? '' }).trim() }))
        break
      case 'agent.done':
        if (!top) setActivity((a) => ({ subs: Math.max(0, a.subs - 1), label: t('子代理结束（{{status}}，{{n}} 轮）', { status: e.data.status, n: e.data.turns }) }))
        break
      case 'llm.stalled':
        setNotes((n) => [...n, t('连接 {{n}} 秒没有任何响应，正在重试', { n: Math.round(e.data.quiet_s) })])
        break
      case 'llm.text.delta':
        if (top) setLive((l) => ({ ...l, text: l.text + e.data.text }))
        break
      case 'llm.reasoning.delta':
        if (top) setLive((l) => ({ ...l, reasoning: l.reasoning + e.data.text }))
        break
      case 'agent.message':
        if (top) {
          setMessages((m) => [...m, e.data.message])
          if (e.data.message.role === 'assistant') setLive({ text: '', reasoning: '' })
        }
        break
      case 'tool.call':
        if (top) setRunning((r) => ({ ...r, [e.data.id]: true }))
        else setActivity((a) => ({ ...a, label: t('子代理正在用「{{tool}}」', { tool: toolLabel(e.data.name) }) }))
        break
      case 'tool.result':
        if (top) {
          setRunning((r) => ({ ...r, [e.data.id]: false }))
          setResults((r) => ({ ...r, [e.data.id]: { preview: e.data.preview, is_error: e.data.is_error, duration: e.data.duration_s } }))
        }
        break
      case 'llm.response':
        if (top && e.data.purpose === 'turn') setUsage({ context: e.data.context_tokens, window: e.data.context_window })
        if (!top) setActivity((a) => ({ ...a, label: t('子代理第 {{n}} 轮', { n: e.data.turn }) }))
        break
      case 'interaction.request':
        setPending((p) => [...p.filter((x) => x.id !== e.data.id), e.data as Interaction])
        break
      case 'interaction.resolved':
        setPending((p) => p.filter((x) => x.id !== e.data.id))
        break
      case 'conversation.status':
        setStatus(e.data.status)
        if (e.data.status === 'running') setNotes([])
        if (e.data.status === 'idle') {
          setLive({ text: '', reasoning: '' })
          setActivity({ subs: 0, label: '' })
          void qc.invalidateQueries({ queryKey: ['conversation', id] })
        }
        break
      case 'agent.error':
        if (top) toast.error(e.data.error)
        setNotes((n) => [...n, String(e.data.error)])
        break
    }
  })

  // Follow the stream only while the user is at the bottom, so reading earlier output is not interrupted.
  useEffect(() => {
    if (following.current) bottom.current?.scrollIntoView({ block: 'end' })
  }, [messages.length, live.text.length, pending.length])

  useEffect(() => {
    if (status !== 'running') return
    const timer = setInterval(() => setQuiet(Math.round((Date.now() - lastEvent.current) / 1000)), 1000)
    return () => clearInterval(timer)
  }, [status])

  if (error) return <div className="flex-1 p-8"><Empty title={t('对话不存在')} hint={errorMessage(error)} /></div>
  if (!data) return <div className="flex flex-1 items-center justify-center"><Spinner /></div>
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <ChatHeader conv={{ ...data, status }} usage={usage} />
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto" onScroll={() => {
        const el = scroller.current
        if (el) following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
      }}>
        <div className="mx-auto flex max-w-3xl flex-col gap-4 px-6 py-6">
          {data.temporary && (
            <div className="rounded-md border border-dashed border-warning/50 bg-warning/6 px-3 py-2 text-xs text-muted">
              <Ghost className="mr-1 inline h-3.5 w-3.5 text-warning" />{t('临时对话：内容只保存在内存里，不读写任何记忆；删除或退出应用后丢失。')}
            </div>
          )}
          <MessageList messages={messages} running={running} results={results} convId={id} memoryMode={data.memory_mode} />
          {status === 'running' && (live.reasoning || live.text) && (
            <div className="flex gap-3">
              <Avatar role="assistant" />
              <div className="min-w-0 flex-1">
                {live.reasoning && <Reasoning text={live.reasoning} streaming />}
                {live.text && <Markdown className="a4s-caret text-sm" text={live.text} />}
              </div>
            </div>
          )}
          {status === 'running' && (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 pl-10 text-xs text-muted">
              <Spinner className="h-3.5 w-3.5" />
              {activity.subs > 0 ? t('{{n}} 个子代理运行中', { n: activity.subs })
                : Object.values(running).some(Boolean) ? t('正在执行工具…')
                  : live.text || live.reasoning ? t('生成中…') : t('思考中…')}
              {activity.label && <span className="text-subtle">· {activity.label}</span>}
              {quiet >= 10 && <span className="text-subtle tabular-nums">· {t('已等待 {{n}} 秒', { n: quiet })}</span>}
            </div>
          )}
          {notes.map((n, i) => <div key={i} className="text-center text-[11px] text-subtle">— {n} —</div>)}
          {pending.map((item) => <InteractionCard key={item.id} item={item} />)}
          <div ref={bottom} />
        </div>
      </div>
      <Composer conv={data} running={status === 'running'} />
    </div>
  )
}

function Avatar({ role }: { role: 'user' | 'assistant' }) {
  return role === 'user' ? (
    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-2 text-muted"><UserRound className="h-3.5 w-3.5" /></div>
  ) : (
    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary"><Sparkles className="h-3.5 w-3.5" /></div>
  )
}

function Reasoning({ text, streaming }: { text: string; streaming?: boolean }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(!!streaming)
  return (
    <div className="mb-2">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1 text-xs text-subtle hover:text-muted">
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <Brain className="h-3 w-3" />{streaming ? t('思考中…') : t('思考摘要')}
      </button>
      {open && <div className="mt-1 border-l-2 border-border pl-3 text-xs leading-relaxed whitespace-pre-wrap text-muted">{text}</div>}
    </div>
  )
}

function textOf(blocks: Block[]): string {
  return blocks.filter((b) => b.type === 'text').map((b) => (b as { text: string }).text).join('')
}

function MessageList({ messages, running, results, convId, memoryMode }: {
  messages: ChatMessage[]
  running: Record<string, boolean>
  results: Record<string, { preview: string; is_error: boolean; duration: number }>
  convId: string
  memoryMode: MemoryMode
}) {
  const { t } = useTranslation()
  const resultBlocks = useMemo(() => {
    const map: Record<string, Extract<Block, { type: 'tool_result' }>> = {}
    for (const m of messages) for (const b of m.content) if (b.type === 'tool_result') map[b.call_id] = b
    return map
  }, [messages])

  return (
    <>
      {messages.map((m, idx) => {
        const kind = m.meta?.kind
        if (kind === 'notice') {
          const resumable = m.meta?.action === 'resume' && idx === messages.length - 1
          return (
            <div key={idx} className="flex items-center gap-2 rounded-md border border-warning/40 bg-warning/8 px-3 py-2 text-xs text-muted">
              <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-warning" />
              <span className="min-w-0 flex-1">{textOf(m.content)}</span>
              {resumable && (
                <Button size="sm" variant="outline" onClick={() => post(`/conversations/${convId}/messages`, { text: '继续' })}>
                  {t('继续')}
                </Button>
              )}
            </div>
          )
        }
        if (m.role === 'user') {
          const hasResults = m.content.some((b) => b.type === 'tool_result')
          const text = textOf(m.content)
          if (kind === 'summary') {
            return (
              <details key={idx} className="rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted">
                <summary className="cursor-pointer">{t('上下文已压缩为摘要（点击查看）')}</summary>
                <Markdown className="mt-2 text-xs" text={text} />
              </details>
            )
          }
          if (kind === 'continue' || kind === 'repair') return <div key={idx} className="text-center text-[11px] text-subtle">— {kind === 'repair' ? t('上次的工具调用被中断') : t('输出被截断，继续生成')} —</div>
          if (hasResults) {
            return text ? <div key={idx} className="text-center text-[11px] text-subtle">— {text.slice(0, 200)} —</div> : null
          }
          return (
            <div key={idx} className="group flex flex-row-reverse gap-3">
              <Avatar role="user" />
              <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-primary/10 px-3.5 py-2 text-sm">
                <Markdown text={text} />
              </div>
              <RememberButton convId={convId} text={text} memoryMode={memoryMode} />
            </div>
          )
        }
        const reasoning = m.content.filter((b) => b.type === 'reasoning').map((b) => (b as { text: string }).text).join('\n')
        const text = textOf(m.content)
        const calls = m.content.filter((b) => b.type === 'tool_call') as Extract<Block, { type: 'tool_call' }>[]
        if (!text && !reasoning && !calls.length) return null
        return (
          <div key={idx} className="group flex gap-3">
            <Avatar role="assistant" />
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              {reasoning && <Reasoning text={reasoning} />}
              {text && <Markdown className="text-sm" text={text} />}
              {calls.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  {calls.map((c) => {
                    const block = resultBlocks[c.id]
                    const live = results[c.id]
                    const view: ToolCallView = {
                      id: c.id, name: c.name, input: c.input, running: running[c.id] === true,
                      duration: live?.duration,
                      result: block ? { text: textOf(block.content), is_error: block.is_error, images: block.content.filter((b) => b.type === 'image') }
                        : live ? { text: live.preview, is_error: live.is_error } : undefined,
                    }
                    return <ToolCallCard key={c.id} call={view} />
                  })}
                </div>
              )}
            </div>
            {text && <RememberButton convId={convId} text={text} memoryMode={memoryMode} />}
          </div>
        )
      })}
    </>
  )
}

function RememberButton({ convId, text, memoryMode }: { convId: string; text: string; memoryMode: MemoryMode }) {
  const { t } = useTranslation()
  if (memoryMode === 'none' || !text.trim()) return <div className="w-7 shrink-0" />
  return (
    <Tip content={t('记住这条')}>
      <button className="mt-1 h-6 w-6 shrink-0 rounded p-1 text-subtle opacity-0 transition group-hover:opacity-100 hover:bg-surface-2 hover:text-fg"
        onClick={async () => {
          try {
            const item = await post<{ name: string; scope: string }>(`/conversations/${convId}/remember`, { text: text.slice(0, 4000) })
            toast.success(t('已记住（{{scope}}）：{{name}}', { scope: item.scope, name: item.name }))
          } catch (err) {
            toast.error(errorMessage(err))
          }
        }}>
        <BookmarkPlus className="h-3.5 w-3.5" />
      </button>
    </Tip>
  )
}

function ChatHeader({ conv, usage }: { conv: Conversation; usage: { context: number; window: number } | null }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { data: models } = useModels()
  const { data: harnesses } = useQuery({ queryKey: ['harnesses'], queryFn: () => get<HarnessProfile[]>('/harnesses') })
  const [title, setTitle] = useState(conv.title)
  const [editingTitle, setEditingTitle] = useState(false)
  const [memoryOps, setMemoryOps] = useState<any[] | null>(null)
  const [proposing, setProposing] = useState(false)
  useEffect(() => setTitle(conv.title), [conv.title])

  const update = async (body: Partial<Conversation>) => {
    try {
      await patch(`/conversations/${conv.id}`, body)
      await qc.invalidateQueries({ queryKey: ['conversation', conv.id] })
      await qc.invalidateQueries({ queryKey: ['conversations'] })
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }
  const modes = MEMORY_MODES.filter((m) => m.value !== 'project' || conv.project_id)
  const pct = usage && usage.window ? Math.min(100, (usage.context / usage.window) * 100) : 0

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border px-5 py-2.5">
      {editingTitle ? (
        <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)}
          onBlur={() => { setEditingTitle(false); if (title !== conv.title) void update({ title }) }}
          onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') { setTitle(conv.title); setEditingTitle(false) } }}
          className="min-w-[8rem] flex-1 basis-40 bg-transparent text-sm font-semibold outline-none focus:underline" />
      ) : (
        <button onClick={() => setEditingTitle(true)} title={t('点击重命名')}
          className="min-w-[8rem] flex-1 basis-40 text-left text-sm font-semibold">
          <Marquee>{conv.title}</Marquee>
        </button>
      )}
      {conv.temporary ? (
        <Badge tone="warning"><Ghost className="h-3 w-3" />{t('临时 · 无记忆')}</Badge>
      ) : (
        <Tip content={t('记忆模式：切换后会重建系统提示（该对话的缓存失效一次）')}>
          <div>
            <Select className="h-7 w-auto text-xs" value={conv.memory_mode} onChange={(e) => update({ memory_mode: e.target.value as MemoryMode })}>
              {modes.map((m) => <option key={m.value} value={m.value}>{t(m.label)}</option>)}
            </Select>
          </div>
        </Tip>
      )}
      <Tip content={t('Harness：角色、工具、技能与权限的组合')}>
        <div className="flex items-center gap-1">
          <Layers className="h-3.5 w-3.5 text-subtle" />
          <Select className="h-7 w-auto max-w-36 text-xs" value={conv.harness_id} onChange={(e) => update({ harness_id: e.target.value })}>
            {harnesses?.map((h) => <option key={h.id} value={h.id}>{t(h.name)}</option>)}
          </Select>
        </div>
      </Tip>
      <Select className="h-7 w-auto max-w-40 text-xs" value={conv.model_id ?? ''} onChange={(e) => update({ model_id: (e.target.value || null) as any })}>
        <option value="">{t('默认模型')}</option>
        {models?.models.map((m) => <option key={m.id} value={m.id}>{t(m.name)}</option>)}
      </Select>
      {usage && (
        <Tip content={t('上下文占用 {{a}} / {{b}} tokens', { a: formatTokens(usage.context), b: formatTokens(usage.window) })}>
          <div className="flex w-16 flex-col gap-0.5">
            <div className="h-1.5 overflow-hidden rounded-full bg-surface-2"><div className={cn('h-full rounded-full', pct > 70 ? 'bg-warning' : 'bg-primary')} style={{ width: `${pct}%` }} /></div>
            <span className="text-[10px] text-subtle">{pct.toFixed(0)}%</span>
          </div>
        </Tip>
      )}
      <Tip content={conv.cost_unknown ? t('所用模型没有设置价格，无法统计费用（在“模型”页填写单价）')
        : t('输入 {{i}} · 输出 {{o}} tokens', { i: formatTokens(conv.tokens?.input), o: formatTokens(conv.tokens?.output) })}>
        <span className="text-xs text-muted tabular-nums">{costLabel(conv.cost_usd, conv.cost_unknown)}</span>
      </Tip>
      {!conv.temporary && conv.memory_mode !== 'none' && (
        <Tip content={t('让模型从这段对话中提议要记住的内容')}>
          <Button size="sm" variant="ghost" loading={proposing} onClick={async () => {
            setProposing(true)
            try {
              setMemoryOps(await post<any[]>(`/conversations/${conv.id}/memory/propose`))
            } catch (err) {
              toast.error(errorMessage(err))
            } finally {
              setProposing(false)
            }
          }}><Brain className="h-3.5 w-3.5" />{t('整理记忆')}</Button>
        </Tip>
      )}
      {conv.temporary && (
        <Button size="sm" variant="outline" onClick={async () => {
          await post(`/conversations/${conv.id}/persist`)
          await qc.invalidateQueries({ queryKey: ['conversation', conv.id] })
          await qc.invalidateQueries({ queryKey: ['conversations'] })
          toast.success(t('已转为正式对话'))
        }}><Save className="h-3.5 w-3.5" />{t('转为正式对话')}</Button>
      )}
      <Tip content={t('归档')}>
        <Button size="icon" variant="ghost" onClick={() => update({ archived: true }).then(() => navigate('chat'))}><Archive className="h-3.5 w-3.5" /></Button>
      </Tip>
      <Button size="icon" variant="ghost" className="text-danger" onClick={async () => {
        if (!(await confirmAction({ title: t('删除这个对话？'), description: t('它的对话级记忆也会一起删除。'), danger: true }))) return
        await del(`/conversations/${conv.id}`)
        await qc.invalidateQueries({ queryKey: ['conversations'] })
        navigate('chat')
      }}><Trash2 className="h-3.5 w-3.5" /></Button>
      {memoryOps && <MemoryOpsDialog convId={conv.id} ops={memoryOps} onClose={() => setMemoryOps(null)} />}
    </div>
  )
}

function MemoryOpsDialog({ convId, ops, onClose }: { convId: string; ops: any[]; onClose: () => void }) {
  const { t } = useTranslation()
  const [selected, setSelected] = useState<boolean[]>(() => ops.map(() => true))
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={t('记忆更新提议')} width="max-w-2xl"
      description={t('模型从这段对话中提议了以下记忆变更，勾选要保存的项。')}
      footer={<>
        <Button variant="outline" onClick={onClose}>{t('取消')}</Button>
        <Button variant="primary" disabled={!ops.length} onClick={async () => {
          await post(`/conversations/${convId}/memory/apply`, { ops: ops.filter((_, i) => selected[i]) })
          toast.success(t('已更新记忆'))
          onClose()
        }}>{t('保存所选')}</Button>
      </>}>
      {ops.length === 0 ? <Empty icon={<MessagesSquare className="h-8 w-8" />} title={t('没有值得记住的新内容')} /> : (
        <div className="flex flex-col gap-2">
          {ops.map((op, i) => (
            <label key={i} className="flex gap-3 rounded-md border border-border p-3 text-sm">
              <input type="checkbox" checked={selected[i]} onChange={(e) => setSelected(selected.map((v, j) => (j === i ? e.target.checked : v)))} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 font-medium">{op.name}<Badge>{op.action}</Badge><Badge tone="info">{op.scope}</Badge><Badge>{op.type}</Badge></div>
                {op.description && <div className="mt-0.5 text-xs text-muted">{op.description}</div>}
                {op.body && <div className="mt-1 text-xs whitespace-pre-wrap">{op.body}</div>}
                {op.reason && <div className="mt-1 text-[11px] text-subtle">{t('理由：')}{op.reason}</div>}
              </div>
            </label>
          ))}
        </div>
      )}
    </Dialog>
  )
}

const PATHISH = /^\s*(~\/|\/|[A-Za-z]:[\\/])[^\n]*$/

interface Attachment {
  path: string
  name: string
  missing?: boolean
  size?: number
}

function Composer({ conv, running }: { conv: Conversation; running: boolean }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const id = conv.id
  const [text, setText] = useState('')
  const [files, setFiles] = useState<Attachment[]>([])
  const [dragging, setDragging] = useState(false)
  const [sending, setSending] = useState(false)
  const composing = useRef(false)
  const { data: permissionModes } = useQuery({
    queryKey: ['permission-modes'],
    queryFn: () => get<{ id: string; label: string; hint: string }[]>('/permission-modes'),
  })

  const setPermission = async (mode: string) => {
    try {
      await patch(`/conversations/${id}`, { permission_mode: mode || null })
      await qc.invalidateQueries({ queryKey: ['conversation', id] })
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }

  const addPaths = async (paths: string[]) => {
    const fresh = paths.map((p) => p.trim()).filter((p) => p && !files.some((f) => f.path === p))
    if (!fresh.length) return
    setFiles((list) => [...list, ...fresh.map((p) => ({ path: p, name: p.split(/[\\/]/).pop() || p }))])
    for (const path of fresh) {
      try {
        const info = await get<{ exists: boolean; path: string; name?: string; size?: number }>('/path-info', { path })
        setFiles((list) => list.map((f) => (f.path === path
          ? { path: info.exists ? info.path : path, name: info.name ?? f.name, size: info.size, missing: !info.exists }
          : f)))
      } catch {
        setFiles((list) => list.map((f) => (f.path === path ? { ...f, missing: true } : f)))
      }
    }
  }

  const send = async () => {
    const value = text.trim()
    const paths = files.filter((f) => !f.missing).map((f) => f.path)
    if (!value && !paths.length) return
    setSending(true)
    try {
      const r = await post<{ status: string }>(`/conversations/${id}/messages`, { text: value, files: paths })
      setText('')
      setFiles([])
      if (r.status === 'steered') toast.info(t('已作为插话发送，将在下一步生效'))
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="border-t border-border bg-surface/70 px-6 py-3">
      <div className="mx-auto flex max-w-3xl flex-col gap-2">
        {files.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {files.map((f) => (
              <FileChip key={f.path} name={f.name} path={f.path} size={f.size} missing={f.missing}
                onRemove={() => setFiles((l) => l.filter((x) => x.path !== f.path))} />
            ))}
          </div>
        )}
        <div className="flex items-end gap-2">
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={Math.min(8, Math.max(2, text.split('\n').length))}
            onCompositionStart={() => { composing.current = true }}
            onCompositionEnd={() => { composing.current = false }}
            onPaste={(e) => {
              // A pasted file (or a pasted path) becomes an attachment instead of raw text.
              const dropped = [...e.clipboardData.files].map((f) => window.a4s?.pathForFile(f) ?? '').filter(Boolean)
              if (dropped.length) {
                e.preventDefault()
                void addPaths(dropped)
                return
              }
              const pasted = e.clipboardData.getData('text')
              const lines = pasted.split('\n').filter((l) => l.trim())
              if (lines.length && lines.every((l) => PATHISH.test(l))) {
                e.preventDefault()
                void addPaths(lines)
              }
            }}
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              const paths = [...e.dataTransfer.files].map((f) => window.a4s?.pathForFile(f) ?? '').filter(Boolean)
              if (paths.length) void addPaths(paths)
              else {
                const text = e.dataTransfer.getData('text')
                if (PATHISH.test(text)) void addPaths([text])
              }
            }}
            onKeyDown={(e) => {
              // IME (e.g. Chinese pinyin) uses Enter to confirm candidates: don't send while composing.
              if (e.key === 'Enter' && !e.shiftKey && !composing.current && e.keyCode !== 229) {
                e.preventDefault()
                void send()
              }
            }}
            placeholder={running ? t('运行中：发送的内容会作为插话在下一步生效…')
              : t('输入研究问题或指令（Enter 发送，Shift+Enter 换行）；可以拖入文件或粘贴文件路径')}
            className={cn('min-h-[52px] flex-1 resize-none rounded-lg border bg-surface px-3 py-2.5 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20',
              dragging ? 'border-primary border-dashed bg-primary/5' : 'border-border')} />
          <div className="flex flex-col gap-1.5">
            {running && (
              <Button size="sm" variant="outline" onClick={() => post(`/conversations/${id}/stop`)}><Square className="h-3.5 w-3.5" />{t('停止')}</Button>
            )}
            <Button variant="primary" onClick={send} loading={sending} disabled={!text.trim() && !files.length}>
              <Send className="h-4 w-4" />{running ? t('插话') : t('发送')}
            </Button>
          </div>
        </div>
        {/* Permission sits here, next to the button that actually sets the agent going. */}
        <div className="flex items-center justify-end gap-1.5">
          <Tip content={t('权限：Agent 动手前要不要先问你')}>
            <ShieldCheck className="h-3.5 w-3.5 text-subtle" />
          </Tip>
          <Select className="h-6.5 w-auto border-transparent bg-transparent px-1.5 text-[11px] text-muted hover:border-border"
            value={conv.permission_mode ?? ''} onChange={(e) => void setPermission(e.target.value)}>
            <option value="">{t('跟随 Harness')}</option>
            {permissionModes?.map((m) => <option key={m.id} value={m.id} title={m.hint}>{t(m.label)}</option>)}
          </Select>
        </div>
      </div>
    </div>
  )
}
