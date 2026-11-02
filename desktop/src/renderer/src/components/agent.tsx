// Shared renderers for agent activity: tool-call cards, interaction (approval / question / checkpoint) cards.
import { Check, ChevronDown, ChevronRight, CircleAlert, Hand, HelpCircle, Loader2, ShieldAlert, Wrench, X } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { confirmAction } from '@/components/confirm'
import { errorMessage, post } from '@/lib/api'
import type { Block, Interaction } from '@/lib/types'
import { cn, toast } from '@/lib/utils'
import { Markdown } from './Markdown'
import { Badge, Button, Input, Textarea } from './ui'

export const TOOL_LABELS: Record<string, string> = {
  read_file: '读取文件', write_file: '写入文件', edit_file: '编辑文件', list_files: '列出文件', grep: '搜索文件内容',
  shell: '命令行', job_start: '启动后台任务', job_status: '查看任务', job_logs: '任务日志', job_kill: '终止任务',
  run_python: '运行 Python', web_fetch: '抓取网页', web_search: '网页搜索', search_papers: '检索文献', get_paper: '文献详情',
  paper_graph: '引用网络', fetch_fulltext: '获取全文', add_reference: '添加参考文献', check_citations: '检查引用',
  record_finding: '记录发现', query_findings: '查询发现', render_document: '渲染文档', search_neuro_datasets: '检索神经数据集',
  get_dandiset: 'DANDI 数据集', get_openneuro_dataset: 'OpenNeuro 数据集', allen_query: 'Allen 脑图谱', memory: '记忆',
  activate_skill: '激活技能', search_skills: '搜索技能', install_skill: '安装技能', ask_human: '询问研究者', delegate: '子代理',
  submit_result: '提交结果',
}

function inputSummary(name: string, input: Record<string, any>): string {
  const pick = input.path ?? input.command ?? input.query ?? input.id ?? input.name ?? input.url ?? input.task ?? input.code
  if (typeof pick === 'string') return pick.length > 120 ? pick.slice(0, 120) + '…' : pick
  if (name === 'memory') return `${input.command} ${input.path ?? input.new_path ?? ''}`
  const text = JSON.stringify(input)
  return text.length > 120 ? text.slice(0, 120) + '…' : text
}

export interface ToolCallView {
  id: string
  name: string
  input: Record<string, any>
  result?: { text: string; is_error: boolean; images?: Block[] }
  running?: boolean
  duration?: number
}

export function ToolCallCard({ call }: { call: ToolCallView }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const label = TOOL_LABELS[call.name] ? t(TOOL_LABELS[call.name]) : call.name.startsWith('mcp__') ? call.name.split('__').slice(1).join(' · ') : call.name
  const status = call.running ? 'running' : call.result?.is_error ? 'error' : call.result ? 'done' : 'pending'
  return (
    <div className={cn('rounded-md border text-xs', status === 'error' ? 'border-danger/35 bg-danger/5' : 'border-border bg-surface-2/60')}>
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left">
        {open ? <ChevronDown className="h-3 w-3 shrink-0 text-subtle" /> : <ChevronRight className="h-3 w-3 shrink-0 text-subtle" />}
        {status === 'running' ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" /> :
          status === 'error' ? <CircleAlert className="h-3.5 w-3.5 shrink-0 text-danger" /> :
            <Wrench className="h-3.5 w-3.5 shrink-0 text-muted" />}
        <span className="shrink-0 font-medium">{label}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted">{inputSummary(call.name, call.input)}</span>
        {call.duration !== undefined && <span className="shrink-0 text-subtle">{call.duration.toFixed(1)}s</span>}
      </button>
      {open && (
        <div className="flex flex-col gap-2 border-t border-border px-2.5 py-2">
          <pre className="max-h-48 overflow-auto rounded bg-code p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
            {JSON.stringify(call.input, null, 2)}
          </pre>
          {call.result && (
            <pre className={cn('max-h-72 overflow-auto rounded p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap',
              call.result.is_error ? 'bg-danger/8 text-danger' : 'bg-code')}>{call.result.text}</pre>
          )}
        </div>
      )}
      {call.result?.images?.map((img, i) => img.type === 'image' && (
        <img key={i} src={`data:${img.media_type};base64,${img.data}`} className="mx-2.5 mb-2 max-h-72 rounded border border-border bg-white" />
      ))}
    </div>
  )
}

export function InteractionCard({ item, onDone }: { item: Interaction; onDone?: () => void }) {
  const { t } = useTranslation()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const p = item.payload
  const respond = async (response: Record<string, any>) => {
    setBusy(true)
    try {
      await post(`/interactions/${item.id}`, response)
      onDone?.()
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }
  if (item.kind === 'approval') {
    return (
      <div className="rounded-lg border border-warning/40 bg-warning/8 p-3 text-sm">
        <div className="flex items-center gap-2 font-medium"><ShieldAlert className="h-4 w-4 text-warning" />{item.title}</div>
        {p.reason && <div className="mt-1 text-xs text-muted">{t('原因：')}{p.reason}</div>}
        <pre className="mt-2 max-h-40 overflow-auto rounded bg-code p-2 font-mono text-[11px] whitespace-pre-wrap">
          {p.preview ?? JSON.stringify(p.input, null, 2)}
        </pre>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button size="sm" variant="primary" loading={busy} onClick={() => respond({ decision: 'allow' })}><Check className="h-3.5 w-3.5" />{t('允许')}</Button>
          <Button size="sm" loading={busy} onClick={() => respond({ decision: 'allow', always: true })}>{t('本次对话都允许')}</Button>
          <Input className="h-7 max-w-56 text-xs" placeholder={t('拒绝理由（可选）')} value={text} onChange={(e) => setText(e.target.value)} />
          <Button size="sm" variant="danger" loading={busy} onClick={() => respond({ decision: 'deny', note: text })}><X className="h-3.5 w-3.5" />{t('拒绝')}</Button>
        </div>
      </div>
    )
  }
  if (item.kind === 'question') {
    const questions: AskedQuestion[] = Array.isArray(p.questions) && p.questions.length
      ? p.questions
      : [{ question: item.title, options: p.options ?? [] }]
    return <QuestionCard questions={questions} context={p.context} busy={busy} onRespond={respond} />
  }
  // checkpoint (loop runs)
  const options: string[] = p.options ?? []
  const choiceMode = p.kind === 'choice'
  return (
    <div className="rounded-lg border border-primary/40 bg-primary/6 p-3 text-sm">
      <div className="flex items-center gap-2 font-medium"><Hand className="h-4 w-4 text-primary" />{item.title}</div>
      {p.message && <Markdown className="mt-2 max-h-56 overflow-auto text-sm" text={String(p.message)} />}
      {p.files?.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{p.files.map((f: string) => <Badge key={f}>{f}</Badge>)}</div>}
      {choiceMode && options.length > 0 && (
        <div className="mt-3 flex flex-col gap-1.5">
          {options.map((o, i) => (
            <Button key={i} size="sm" className="justify-start text-left whitespace-normal" loading={busy}
              onClick={() => respond({ action: 'approve', choice: o, feedback: text })}>{i + 1}. {o}</Button>
          ))}
        </div>
      )}
      <Textarea rows={2} className="mt-3 text-xs" placeholder={t('反馈或补充说明（可选）')} value={text} onChange={(e) => setText(e.target.value)} />
      <div className="mt-2 flex flex-wrap gap-2">
        {!choiceMode && <Button size="sm" variant="primary" loading={busy} onClick={() => respond({ action: 'approve', feedback: text })}><Check className="h-3.5 w-3.5" />{t('批准继续')}</Button>}
        {!choiceMode && <Button size="sm" loading={busy} disabled={!text} onClick={() => respond({ action: 'feedback', feedback: text })}>{t('按反馈重做')}</Button>}
        <Button size="sm" variant="danger" loading={busy} onClick={async () => {
          if (await confirmAction({ title: t('确定终止整个运行？'), danger: true, confirmLabel: t('终止运行') })) respond({ action: 'abort' })
        }}>{t('终止运行')}</Button>
      </div>
    </div>
  )
}

interface AskedQuestion {
  question: string
  options?: string[] | null
  multi?: boolean
}

/** The agent may ask several things at once; every question can be answered by picking options or writing your own. */
function QuestionCard({ questions, context, busy, onRespond }: {
  questions: AskedQuestion[]
  context?: string
  busy: boolean
  onRespond: (response: Record<string, any>) => void
}) {
  const { t } = useTranslation()
  const [picked, setPicked] = useState<string[][]>(() => questions.map(() => []))
  const [own, setOwn] = useState<string[]>(() => questions.map(() => ''))
  const answerOf = (i: number) => [...picked[i], own[i].trim()].filter(Boolean).join('；')
  const ready = questions.every((_, i) => answerOf(i))
  const toggle = (i: number, option: string, multi: boolean) => {
    setPicked((rows) => rows.map((row, j) => {
      if (j !== i) return row
      if (row.includes(option)) return row.filter((o) => o !== option)
      return multi ? [...row, option] : [option]
    }))
  }
  const submit = () => onRespond({ answers: questions.map((_, i) => answerOf(i)), answer: answerOf(0) })

  return (
    <div className="rounded-lg border border-info/40 bg-info/8 p-3 text-sm">
      <div className="flex items-center gap-2 font-medium">
        <HelpCircle className="h-4 w-4 text-info" />
        {questions.length > 1 ? t('Agent 有 {{n}} 个问题', { n: questions.length }) : t('Agent 在询问你')}
      </div>
      {context && <div className="mt-1 text-xs whitespace-pre-wrap text-muted">{context}</div>}
      <div className="mt-2 flex flex-col gap-3">
        {questions.map((q, i) => (
          <div key={i} className={cn(questions.length > 1 && 'rounded-md border border-border/70 bg-surface/40 p-2.5')}>
            <Markdown className="text-sm" text={questions.length > 1 ? `${i + 1}. ${q.question}` : q.question} />
            {q.multi && <div className="mt-0.5 text-[11px] text-subtle">{t('可多选')}</div>}
            {(q.options?.length ?? 0) > 0 && (
              <div className="mt-2 flex flex-col gap-1.5">
                {q.options!.map((o) => (
                  <button key={o} type="button" disabled={busy} onClick={() => toggle(i, o, !!q.multi)}
                    className={cn('rounded-md border px-2.5 py-1.5 text-left text-xs transition',
                      picked[i].includes(o) ? 'border-primary bg-primary/15 text-fg' : 'border-border bg-surface hover:bg-surface-2')}>
                    {picked[i].includes(o) && <Check className="mr-1 inline h-3 w-3 text-primary" />}{o}
                  </button>
                ))}
              </div>
            )}
            <Input className="mt-2 h-8" placeholder={(q.options?.length ?? 0) > 0 ? t('或自己填写 / 补充说明…') : t('输入回答…')}
              value={own[i]} onChange={(e) => setOwn((rows) => rows.map((v, j) => (j === i ? e.target.value : v)))}
              onKeyDown={(e) => { if (e.key === 'Enter' && ready && questions.length === 1) submit() }} />
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <Button size="sm" variant="primary" disabled={!ready} loading={busy} onClick={submit}>{t('回答')}</Button>
        <span className="text-[11px] text-subtle">{ready ? '' : t('每个问题都要选一个或自己填写')}</span>
      </div>
    </div>
  )
}
