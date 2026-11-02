import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Check, ChevronDown, ChevronRight, KeyRound, Pencil, Plus, Star, Trash2, X, Zap } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { confirmAction } from '@/components/confirm'
import { Badge, Button, Card, Dialog, Empty, Field, Input, PageHeader, SectionTitle, Select, Switch, Textarea, Tip } from '@/components/ui'
import { del, errorMessage, get, post, put } from '@/lib/api'
import { useModels } from '@/lib/store'
import type { Capabilities, ModelProfile, Preset } from '@/lib/types'
import { cn, toast } from '@/lib/utils'

const ROLES: [string, string][] = [
  ['librarian', '文献专员'], ['analyst', '数据分析师'], ['experimenter', '实验员'], ['writer', '写作者'], ['reviewer', '评审'],
  ['scoper', '研究简报'], ['hypothesizer', '想法生成'], ['designer', '方案设计'],
]
const CAP_LABELS: Record<keyof Capabilities, string> = {
  tools: '工具调用', parallel_tools: '并行工具', vision: '图像输入', thinking: '自适应思考', tool_result_images: '工具结果含图片',
  native_web_search: '原生网页搜索', native_memory_tool: '原生记忆工具', stream_usage: '流式用量统计', echo_reasoning_content: '回传推理内容',
}

export default function ModelsPage() {
  const { t } = useTranslation()
  const { data } = useModels()
  const qc = useQueryClient()
  const [editing, setEditing] = useState<ModelProfile | 'new' | null>(null)
  const [testing, setTesting] = useState<string | null>(null)
  const [results, setResults] = useState<Record<string, any>>({})
  const refresh = () => qc.invalidateQueries({ queryKey: ['models'] })

  const test = async (m: ModelProfile) => {
    setTesting(m.id)
    try {
      const r = await post<any>(`/models/${m.id}/test`)
      setResults({ ...results, [m.id]: r })
      if (r.ok) toast.success(t('连接成功：{{model}}（{{s}} 秒）', { model: r.served_model, s: r.latency_s }))
      else toast.error(t('连接失败：{{err}}', { err: r.error }))
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setTesting(null)
    }
  }

  const models = data?.models ?? []
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title={t('模型')} description={t('可以添加任意多个模型：每个模型单独设置 base URL、API Key、模型名与参数。Key 保存在系统钥匙串。')}
        actions={<Button variant="primary" onClick={() => setEditing('new')}><Plus className="h-4 w-4" />{t('添加模型')}</Button>} />
      <div className="flex-1 overflow-y-auto px-6 py-5">
        {models.length === 0 ? (
          <Empty icon={<Bot className="h-10 w-10" />} title={t('还没有模型')}
            hint={t('添加一个 Anthropic、OpenAI 或任意 OpenAI 兼容端点（DeepSeek、通义千问、Kimi、智谱、Ollama…）。也可以在“设置 → 通用”里启用演示模型先体验。')}
            action={<Button variant="primary" onClick={() => setEditing('new')}><Plus className="h-4 w-4" />{t('添加模型')}</Button>} />
        ) : (
          <div className="grid gap-3 xl:grid-cols-2">
            {models.map((m) => {
              const isDefault = data?.default === m.id
              const r = results[m.id]
              return (
                <Card key={m.id} className={cn('flex flex-col gap-3 p-4', isDefault && 'border-primary/50')}>
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5 rounded-md bg-primary/12 p-2 text-primary"><Bot className="h-4 w-4" /></div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate font-semibold">{t(m.name)}</span>
                        {isDefault && <Badge tone="primary"><Star className="h-3 w-3" />{t('默认')}</Badge>}
                        {!m.enabled && <Badge>{t('已停用')}</Badge>}
                      </div>
                      <div className="mt-0.5 truncate font-mono text-xs text-muted">{m.provider} · {m.model}</div>
                      {m.base_url && <div className="truncate font-mono text-[11px] text-subtle">{m.base_url}</div>}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {m.provider !== 'fake' && (m.key.resolved
                      ? <Badge tone="success"><KeyRound className="h-3 w-3" />{m.key.stored ? m.key.masked : m.key.env ? `env:${m.key.env}` : t('无需 Key')}</Badge>
                      : <Badge tone="warning"><KeyRound className="h-3 w-3" />{t('缺少 API Key')}</Badge>)}
                    {(Object.keys(CAP_LABELS) as (keyof Capabilities)[]).filter((k) => m.capabilities[k] && ['thinking', 'vision', 'native_web_search', 'native_memory_tool'].includes(k))
                      .map((k) => <Badge key={k} tone="info">{t(CAP_LABELS[k])}</Badge>)}
                    <Badge>{t('上下文 {{n}}K', { n: Math.round(m.context_window / 1000) })}</Badge>
                    {m.pricing && <Badge>${m.pricing.input}/${m.pricing.output} / M</Badge>}
                    {m.provider !== 'fake' && m.pricing_known === false && (
                      <Tip content={t('没有单价就无法统计费用，预算上限也不会生效。点“编辑 → 高级设置”填写输入/输出价格。')}>
                        <span><Badge tone="warning">{t('未设置价格')}</Badge></span>
                      </Tip>
                    )}
                  </div>
                  {r && (
                    <div className={cn('rounded-md border px-3 py-2 text-xs', r.ok ? 'border-success/30 bg-success/8' : 'border-danger/30 bg-danger/8')}>
                      <div className="flex items-start gap-1.5">
                        {r.ok ? <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" /> : <X className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />}
                        <span className="min-w-0">
                          {r.ok ? t('可用（{{s}} 秒），工具调用：{{tools}}', { s: r.latency_s, tools: r.tools_ok === undefined ? '—' : r.tools_ok ? t('支持') : t('未触发') })
                            : `${r.kind ?? ''} ${r.error ?? ''}`}
                        </span>
                      </div>
                      {r.ok && r.tools_note && <div className="mt-1 text-warning">{t(r.tools_note)}</div>}
                      {r.ok && r.model_capabilities && (
                        <div className="mt-1 text-muted">{t('模型能力：{{caps}} · 上下文 {{n}}', { caps: r.model_capabilities.join(', '), n: r.context })}</div>
                      )}
                    </div>
                  )}
                  {m.id !== 'demo' && (
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" onClick={() => test(m)} loading={testing === m.id}><Zap className="h-3.5 w-3.5" />{t('测试连接')}</Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditing(m)}><Pencil className="h-3.5 w-3.5" />{t('编辑')}</Button>
                      {!isDefault && <Button size="sm" variant="ghost" onClick={async () => { await put('/models-defaults', { default: m.id }); void refresh() }}>
                        <Star className="h-3.5 w-3.5" />{t('设为默认')}</Button>}
                      <Button size="sm" variant="ghost" className="ml-auto text-danger" onClick={async () => {
                        if (!(await confirmAction({ title: t('删除模型「{{name}}」？', { name: m.name }), danger: true }))) return
                        await del(`/models/${m.id}`)
                        void refresh()
                      }}><Trash2 className="h-3.5 w-3.5" /></Button>
                    </div>
                  )}
                </Card>
              )
            })}
          </div>
        )}
        {models.length > 0 && <RoleMapping />}
      </div>
      {editing && <ModelDialog model={editing === 'new' ? null : editing} onClose={() => { setEditing(null); void refresh() }} />}
    </div>
  )
}

function RoleMapping() {
  const { t } = useTranslation()
  const { data } = useModels()
  const qc = useQueryClient()
  if (!data) return null
  return (
    <div className="mt-8 max-w-3xl">
      <SectionTitle>{t('角色 → 模型')}</SectionTitle>
      <p className="mb-3 text-xs text-muted">{t('Loop 中不同角色可以用不同模型，例如让评审用另一家模型以减少“自己评自己”的偏差。未设置的角色使用默认模型。')}</p>
      <Card className="divide-y divide-border">
        {ROLES.map(([role, label]) => (
          <div key={role} className="flex items-center gap-4 px-4 py-2">
            <span className="w-32 text-sm">{t(label)}</span>
            <Select className="max-w-xs" value={data.roles[role] ?? ''} onChange={async (e) => {
              await put('/models-defaults', { roles: { ...data.roles, [role]: e.target.value } })
              void qc.invalidateQueries({ queryKey: ['models'] })
            }}>
              <option value="">{t('（默认模型）')}</option>
              {data.models.map((m) => <option key={m.id} value={m.id}>{t(m.name)}</option>)}
            </Select>
          </div>
        ))}
      </Card>
    </div>
  )
}

function ModelDialog({ model, onClose }: { model: ModelProfile | null; onClose: () => void }) {
  const { t } = useTranslation()
  const { data: presets } = useQuery({ queryKey: ['presets'], queryFn: () => get<Preset[]>('/models/presets') })
  const [presetId, setPresetId] = useState<string | null>(model ? (model.preset ?? 'custom') : null)
  const preset = presets?.find((p) => p.id === presetId)
  const [form, setForm] = useState<Record<string, any>>(() => model ? { ...model } : {})
  const [apiKey, setApiKey] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const [saving, setSaving] = useState(false)
  const [jsonText, setJsonText] = useState(() => ({
    headers: JSON.stringify(model?.extra_headers ?? {}, null, 2), body: JSON.stringify(model?.extra_body ?? {}, null, 2),
  }))

  const pick = (p: Preset) => {
    setPresetId(p.id)
    setForm({ name: p.label, provider: p.provider, model: p.model, base_url: p.base_url, effort: p.effort,
      context_window: p.context_window, capabilities: { ...p.capabilities } })
  }
  const set = (k: string, v: any) => setForm((f) => ({ ...f, [k]: v }))
  const caps: Capabilities | undefined = form.capabilities

  const save = async () => {
    setSaving(true)
    try {
      const body: Record<string, any> = { ...form }
      body.extra_headers = JSON.parse(jsonText.headers || '{}')
      body.extra_body = JSON.parse(jsonText.body || '{}')
      for (const k of ['max_tokens', 'temperature', 'context_window', 'timeout_s']) {
        if (body[k] === '' || body[k] === undefined) body[k] = k === 'context_window' ? 128000 : null
        else if (body[k] !== null) body[k] = Number(body[k])
      }
      delete body.key
      if (model) await put(`/models/${model.id}`, { ...body, ...(apiKey ? { api_key: apiKey } : {}) })
      else await post('/models', { ...body, preset: presetId, api_key: apiKey || undefined })
      toast.success(t('已保存模型'))
      onClose()
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-2xl"
      title={model ? t('编辑模型') : presetId ? t('配置模型') : t('选择模型服务商')}
      footer={presetId && <>
        <Button variant="outline" onClick={onClose}>{t('取消')}</Button>
        <Button variant="primary" onClick={save} loading={saving}><Check className="h-4 w-4" />{t('保存')}</Button>
      </>}>
      {!presetId ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {presets?.map((p) => (
            <button key={p.id} onClick={() => pick(p)}
              className="flex flex-col gap-1 rounded-lg border border-border p-3 text-left transition hover:border-primary hover:bg-primary/5">
              <span className="text-sm font-medium">{t(p.label)}</span>
              <span className="truncate font-mono text-[11px] text-muted">{p.base_url ?? (p.provider === 'anthropic' ? 'api.anthropic.com' : 'api.openai.com')}</span>
            </button>
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label={t('显示名称')}><Input value={form.name ?? ''} onChange={(e) => set('name', e.target.value)} /></Field>
            <Field label={t('接口类型')}>
              <Select value={form.provider} onChange={(e) => set('provider', e.target.value)} disabled={!!model && model.provider === 'fake'}>
                <option value="anthropic">Anthropic Messages API</option>
                <option value="openai">OpenAI</option>
                <option value="openai-compatible">{t('OpenAI 兼容端点')}</option>
                <option value="ollama">{t('Ollama（本地原生接口）')}</option>
              </Select>
            </Field>
            <Field label={t('模型名')} hint={preset?.model_hint}>
              <Input value={form.model ?? ''} onChange={(e) => set('model', e.target.value)} placeholder={preset?.model_hint} className="font-mono" />
            </Field>
            <Field label="Base URL" hint={t('留空使用官方地址')}>
              <Input value={form.base_url ?? ''} onChange={(e) => set('base_url', e.target.value || null)} className="font-mono text-xs"
                placeholder={form.provider === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1'} />
            </Field>
            <Field label="API Key" className="col-span-2"
              hint={model?.key.stored ? t('已保存：{{m}}（留空则不修改）', { m: model.key.masked }) : preset?.needs_key === false ? t('本地端点通常不需要 Key') : t('保存到系统钥匙串，不会写入数据库')}>
              <Input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={preset?.api_key_env ? t('或设置环境变量 {{env}}', { env: preset.api_key_env }) : ''} />
            </Field>
            {form.provider === 'anthropic' ? (
              <Field label={t('推理强度（effort）')}>
                <Select value={form.effort ?? ''} onChange={(e) => set('effort', e.target.value || null)}>
                  <option value="">{t('默认')}</option>
                  {['low', 'medium', 'high', 'xhigh', 'max'].map((v) => <option key={v} value={v}>{v}</option>)}
                </Select>
              </Field>
            ) : (
              <Field label={t('推理强度（reasoning_effort，仅推理模型）')}>
                <Select value={form.reasoning_effort ?? ''} onChange={(e) => set('reasoning_effort', e.target.value || null)}>
                  <option value="">{t('不发送')}</option>
                  {['minimal', 'low', 'medium', 'high'].map((v) => <option key={v} value={v}>{v}</option>)}
                </Select>
              </Field>
            )}
            <Field label={t('上下文长度（tokens）')}>
              <Input type="number" value={form.context_window ?? ''} onChange={(e) => set('context_window', e.target.value)} />
            </Field>
          </div>
          <div className="rounded-lg border border-border bg-surface-2/40 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium">{t('单价（不填就统计不出费用，预算上限也不会生效）')}</span>
              {preset?.pricing_url && (
                <button className="text-xs text-primary underline" onClick={() => window.a4s?.openExternal(preset.pricing_url!)}>
                  {t('打开 {{p}} 的价格页', { p: t(preset.label) })}
                </button>
              )}
              {preset?.docs?.startsWith('http') && (
                <button className="text-xs text-primary underline" onClick={() => window.a4s?.openExternal(preset.docs)}>
                  {t('接口文档')}
                </button>
              )}
            </div>
            <div className="mt-2 grid grid-cols-2 gap-3">
              <Field label={t('输入 $/百万 tokens')}>
                <Input type="number" step="0.01" value={form.pricing?.input ?? ''} placeholder={t('内置或暂不填写')}
                  onChange={(e) => set('pricing', e.target.value === '' ? null : { ...(form.pricing ?? { output: 0 }), input: Number(e.target.value) })} /></Field>
              <Field label={t('输出 $/百万 tokens')}>
                <Input type="number" step="0.01" value={form.pricing?.output ?? ''} placeholder={t('内置或暂不填写')}
                  onChange={(e) => set('pricing', { ...(form.pricing ?? { input: 0 }), output: Number(e.target.value) })} /></Field>
            </div>
          </div>
          <button className="flex items-center gap-1 self-start text-xs text-muted hover:text-fg" onClick={() => setAdvanced(!advanced)}>
            {advanced ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}{t('高级设置')}
          </button>
          {advanced && (
            <div className="flex flex-col gap-4">
              <div className="grid grid-cols-3 gap-3">
                <Field label={t('最大输出 tokens')}><Input type="number" value={form.max_tokens ?? ''} onChange={(e) => set('max_tokens', e.target.value)} placeholder={t('自动')} /></Field>
                <Field label="temperature"><Input type="number" step="0.1" value={form.temperature ?? ''} onChange={(e) => set('temperature', e.target.value)} placeholder={t('不发送')} /></Field>
                <Field label={t('超时（秒）')}><Input type="number" value={form.timeout_s ?? 600} onChange={(e) => set('timeout_s', e.target.value)} /></Field>
                {form.provider !== 'anthropic' && (
                  <Field label={t('最大输出参数名')}>
                    <Select value={form.max_tokens_param ?? 'max_completion_tokens'} onChange={(e) => set('max_tokens_param', e.target.value)}>
                      <option value="max_completion_tokens">max_completion_tokens</option>
                      <option value="max_tokens">max_tokens</option>
                    </Select>
                  </Field>
                )}
                <Field label={t('缓存读取价 $/百万 tokens')} hint={t('留空按输入价计算')}>
                  <Input type="number" step="0.01" value={form.pricing?.cache_read ?? ''}
                    onChange={(e) => set('pricing', { ...(form.pricing ?? { input: 0, output: 0 }), cache_read: e.target.value === '' ? null : Number(e.target.value) })} /></Field>
                <Field label={t('缓存写入价 $/百万 tokens')} hint={t('留空按输入价计算')}>
                  <Input type="number" step="0.01" value={form.pricing?.cache_write ?? ''}
                    onChange={(e) => set('pricing', { ...(form.pricing ?? { input: 0, output: 0 }), cache_write: e.target.value === '' ? null : Number(e.target.value) })} /></Field>
              </div>
              {caps && (
                <div>
                  <div className="mb-2 text-xs font-medium text-muted">{t('能力开关')}</div>
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                    {(Object.keys(CAP_LABELS) as (keyof Capabilities)[]).map((k) => (
                      <Switch key={k} checked={!!caps[k]} onCheckedChange={(v) => set('capabilities', { ...caps, [k]: v })} label={<span className="text-xs">{t(CAP_LABELS[k])}</span>} />
                    ))}
                  </div>
                </div>
              )}
              {form.provider === 'anthropic' && (
                <Switch checked={form.fallbacks !== false} onCheckedChange={(v) => set('fallbacks', v)}
                  label={<span className="text-xs">{t('拒答时服务器端自动切换备用模型（fallbacks=default）')}</span>} />
              )}
              <div className="grid grid-cols-2 gap-3">
                <Field label={t('额外请求头（JSON）')}><Textarea rows={4} className="font-mono text-xs" value={jsonText.headers} onChange={(e) => setJsonText({ ...jsonText, headers: e.target.value })} /></Field>
                <Field label={t('额外请求体字段（JSON）')}><Textarea rows={4} className="font-mono text-xs" value={jsonText.body} onChange={(e) => setJsonText({ ...jsonText, body: e.target.value })} /></Field>
              </div>
              <Switch checked={form.enabled !== false} onCheckedChange={(v) => set('enabled', v)} label={t('启用')} />
            </div>
          )}
        </div>
      )}
    </Dialog>
  )
}
