import { Bot, Brain, Columns3, FileText, Flag, GitBranch, Hand, Play, Repeat, ShieldCheck, Sparkles, StickyNote,
  Terminal, Trophy, Workflow, Wrench, type LucideIcon } from 'lucide-react'

export interface NodeMeta {
  label: string
  icon: LucideIcon
  color: string
  container?: boolean
  description: string
  defaults: Record<string, any>
}

export const NODE_META: Record<string, NodeMeta> = {
  start: { label: '开始', icon: Play, color: 'var(--a4s-fg-subtle)', description: '流程入口', defaults: {} },
  end: { label: '结束', icon: Flag, color: 'var(--a4s-fg-subtle)', description: '流程出口', defaults: {} },
  agent: { label: 'Agent', icon: Bot, color: 'var(--a4s-node-agent)', description: '用 Harness 运行一个 Agent',
    defaults: { harness: 'assistant', prompt: '', outputs: [] } },
  tournament: { label: '想法锦标赛', icon: Trophy, color: 'var(--a4s-node-agent)', description: '生成候选 → 新颖性 → Elo 排序',
    defaults: { prompt: '', n: 6, top_k: 3 } },
  tool: { label: '工具', icon: Wrench, color: 'var(--a4s-node-tool)', description: '直接调用一个工具', defaults: { tool: '', args: {} } },
  command: { label: '命令/验证门', icon: Terminal, color: 'var(--a4s-node-tool)', description: '运行命令，可作为验证门',
    defaults: { command: '', timeout_s: 1800 } },
  human: { label: '人工检查点', icon: Hand, color: 'var(--a4s-node-human)', description: '批准 / 反馈 / 选择',
    defaults: { kind: 'approve', title: '' } },
  condition: { label: '条件分支', icon: GitBranch, color: 'var(--a4s-node-condition)', description: '按表达式选择分支',
    defaults: { expression: '' } },
  loop: { label: '循环', icon: Repeat, color: 'var(--a4s-node-loop)', container: true, description: '重复子图直到条件满足',
    defaults: { max_iterations: 3, until: '' } },
  parallel: { label: '并行展开', icon: Columns3, color: 'var(--a4s-node-loop)', container: true, description: '对列表每项并行执行子图',
    defaults: { items: '[]', max_concurrency: 2 } },
  subflow: { label: '子流程', icon: Workflow, color: 'var(--a4s-node-loop)', description: '调用另一个 Loop', defaults: { loop_id: '' } },
  checks: { label: '规则检查', icon: ShieldCheck, color: 'var(--a4s-node-check)', description: '引用、产物、受保护文件、数字溯源',
    defaults: {} },
  render: { label: '渲染文档', icon: FileText, color: 'var(--a4s-node-tool)', description: '用结果文件填充数字占位符',
    defaults: { source: 'paper/paper.src.md', output: 'paper/paper.md' } },
  memory: { label: '记忆', icon: Brain, color: 'var(--a4s-node-tool)', description: '写入或整理记忆', defaults: { action: 'consolidate' } },
  distill: { label: '技能沉淀', icon: Sparkles, color: 'var(--a4s-node-agent)', description: '从运行中提炼技能（待审批）', defaults: {} },
  note: { label: '注释', icon: StickyNote, color: 'var(--a4s-fg-subtle)', description: '说明文字（不执行）', defaults: { text: '' } },
}

export const PALETTE_ORDER = ['agent', 'tournament', 'human', 'condition', 'loop', 'parallel', 'command', 'tool', 'checks',
  'render', 'memory', 'distill', 'subflow', 'start', 'end', 'note']

export type FieldKind = 'text' | 'textarea' | 'prompt' | 'number' | 'bool' | 'select' | 'list' | 'json' | 'expr'

export interface FieldSpec {
  key: string
  label: string
  kind: FieldKind
  options?: { value: string; label: string }[] | 'harnesses' | 'roles' | 'models' | 'loops' | 'tools'
  hint?: string
}

const ROLE_OPTIONS = [
  { value: '', label: '（沿用 Harness 的角色）' }, { value: 'scoper', label: '研究简报' }, { value: 'librarian', label: '文献专员' },
  { value: 'hypothesizer', label: '想法生成' }, { value: 'designer', label: '方案设计' }, { value: 'experimenter', label: '实验员' },
  { value: 'analyst', label: '数据分析师' }, { value: 'writer', label: '写作者' }, { value: 'reviewer', label: '评审' },
]
export const ROLES = ROLE_OPTIONS

export const FIELDS: Record<string, FieldSpec[]> = {
  agent: [
    { key: 'harness', label: 'Harness', kind: 'select', options: 'harnesses' },
    { key: 'role', label: '角色提示', kind: 'select', options: 'roles' },
    { key: 'prompt', label: '任务提示（可用 {{变量}}）', kind: 'prompt' },
    { key: 'outputs', label: '必须产出的文件', kind: 'list', hint: '未产出时会要求 Agent 补齐' },
    { key: 'output_schema', label: '结构化输出（JSON Schema，可选）', kind: 'json', hint: '设置后 Agent 需调用 submit_result；结果可在后续表达式中通过 nodes.<id>.output.result 使用' },
    { key: 'checkpoint', label: '完成后人工审阅', kind: 'bool' },
    { key: 'model_id', label: '模型（可选）', kind: 'select', options: 'models' },
    { key: 'effort', label: '推理强度', kind: 'select', options: [{ value: '', label: '默认' }, ...['low', 'medium', 'high', 'xhigh', 'max'].map((v) => ({ value: v, label: v }))] },
    { key: 'max_turns', label: '最大轮数', kind: 'number' },
    { key: 'max_cost_usd', label: '预算上限（美元）', kind: 'number' },
    { key: 'tool_groups', label: '工具组覆盖（可选）', kind: 'list', hint: '例如 fs, literature, python' },
    { key: 'continue_on_error', label: '失败时继续', kind: 'bool' },
  ],
  tournament: [
    { key: 'prompt', label: '想法生成提示', kind: 'prompt' },
    { key: 'n', label: '候选数量', kind: 'number' },
    { key: 'top_k', label: '保留前几名', kind: 'number' },
    { key: 'finalists', label: '进入 Elo 两两对比的数量', kind: 'number' },
    { key: 'novelty_check', label: '做新颖性检索', kind: 'bool' },
    { key: 'role', label: '生成角色', kind: 'select', options: 'roles' },
    { key: 'output_file', label: '输出文件', kind: 'text', hint: '默认 plan/hypotheses.md' },
  ],
  tool: [
    { key: 'tool', label: '工具', kind: 'select', options: 'tools' },
    { key: 'args', label: '参数（JSON，字符串中可用 {{变量}}）', kind: 'json' },
    { key: 'continue_on_error', label: '失败时继续', kind: 'bool' },
  ],
  command: [
    { key: 'command', label: '命令（可用 {{变量}}）', kind: 'textarea' },
    { key: 'timeout_s', label: '超时（秒）', kind: 'number' },
    { key: 'metric.file', label: '指标文件（JSON）', kind: 'text' },
    { key: 'metric.pointer', label: 'JSON 指针', kind: 'text', hint: '例如 /accuracy/mean' },
    { key: 'pass_if', label: '通过条件（表达式，可选）', kind: 'expr', hint: '可用 exit_code、metric、stdout；默认退出码为 0 即通过' },
    { key: 'fail_on_error', label: '不通过时终止运行', kind: 'bool' },
  ],
  human: [
    { key: 'title', label: '标题', kind: 'text' },
    { key: 'message', label: '说明（可用 {{变量}}）', kind: 'textarea' },
    { key: 'kind', label: '类型', kind: 'select', options: [{ value: 'approve', label: '批准' }, { value: 'choice', label: '多选一' }, { value: 'input', label: '输入' }] },
    { key: 'options', label: '选项', kind: 'list' },
    { key: 'options_expr', label: '选项表达式（可选）', kind: 'expr', hint: '例如 nodes.ideas.output.options' },
    { key: 'show_files', label: '要展示的文件', kind: 'list' },
  ],
  condition: [{ key: 'expression', label: '条件表达式', kind: 'expr', hint: '出边的条件填 true / false，或另写表达式' }],
  loop: [
    { key: 'max_iterations', label: '最大轮数', kind: 'number' },
    { key: 'until', label: '停止条件（表达式）', kind: 'expr', hint: '每轮结束后求值，例如 nodes.review.output.result.decision == \'accept\'' },
    { key: 'max_cost_usd', label: '循环预算（美元）', kind: 'number' },
    { key: 'metric.name', label: '指标名', kind: 'text' },
    { key: 'metric.file', label: '指标文件', kind: 'text', hint: '例如 results/main.json' },
    { key: 'metric.pointer', label: 'JSON 指针', kind: 'text', hint: '例如 /score' },
    { key: 'metric.goal', label: '优化方向', kind: 'select', options: [{ value: 'max', label: '越大越好' }, { value: 'min', label: '越小越好' }] },
    { key: 'stall_patience', label: '停滞容忍轮数（0 = 不检测）', kind: 'number' },
    { key: 'keep_discard', label: '改进才保留，否则 git 回滚', kind: 'bool' },
  ],
  parallel: [
    { key: 'items', label: '列表表达式', kind: 'expr', hint: '例如 nodes.ideas.output.top 或 range(3)' },
    { key: 'item_var', label: '循环变量名', kind: 'text', hint: '默认 item；序号为 index' },
    { key: 'max_concurrency', label: '最大并发', kind: 'number' },
    { key: 'max_items', label: '最多项数', kind: 'number' },
  ],
  subflow: [
    { key: 'loop_id', label: '子流程', kind: 'select', options: 'loops' },
    { key: 'inputs', label: '输入映射（JSON）', kind: 'json' },
  ],
  checks: [
    { key: 'artifacts', label: '必须存在的文件', kind: 'list' },
    { key: 'json_valid', label: '必须是合法 JSON 的文件', kind: 'list' },
    { key: 'citations', label: '检查引用的文档', kind: 'list' },
    { key: 'placeholders', label: '检查结果占位符的文档', kind: 'list' },
    { key: 'numbers', label: '检查“无出处数字”的文档', kind: 'list' },
    { key: 'findings', label: '检查发现账本证据', kind: 'bool' },
    { key: 'protect', label: '冻结（记录哈希并设为只读）', kind: 'list' },
    { key: 'verify_protected', label: '校验受保护文件未被改动', kind: 'bool' },
    { key: 'fail_on_error', label: '不通过时终止运行', kind: 'bool' },
  ],
  render: [
    { key: 'source', label: '源文件', kind: 'text' },
    { key: 'output', label: '输出文件', kind: 'text' },
  ],
  memory: [
    { key: 'action', label: '操作', kind: 'select', options: [{ value: 'consolidate', label: '从运行中整理记忆' }, { value: 'write', label: '写入一条记忆' }] },
    { key: 'scope', label: '作用域（写入时）', kind: 'select', options: [{ value: 'project', label: '项目' }, { value: 'global', label: '全局' }, { value: 'conversation', label: '本对话/运行' }] },
    { key: 'name', label: '名称（写入时）', kind: 'text' },
    { key: 'description', label: '描述', kind: 'text' },
    { key: 'body', label: '内容（可用 {{变量}}）', kind: 'textarea' },
    { key: 'auto_apply', label: '自动应用整理结果', kind: 'bool' },
  ],
  distill: [],
  note: [{ key: 'text', label: '说明文字', kind: 'textarea' }],
  start: [],
  end: [],
}

type T = (key: string, options?: Record<string, unknown>) => string

export function summarize(type: string, config: Record<string, any>, t: T): string {
  switch (type) {
    case 'agent': return [config.role || config.harness, (config.prompt || '').replace(/\s+/g, ' ').slice(0, 60)].filter(Boolean).join(' · ')
    case 'tournament': return `${config.n ?? 6} → top ${config.top_k ?? 3}`
    case 'command': return (config.command || '').slice(0, 60)
    case 'tool': return config.tool || ''
    case 'human': return config.title || config.kind || ''
    case 'condition': return config.expression || ''
    case 'loop': return [t('≤ {{n}} 轮', { n: config.max_iterations ?? '?' }), config.until && `until ${config.until}`, config.keep_discard && t('保留/丢弃'),
      config.metric?.name && t('指标 {{name}}', { name: config.metric.name })].filter(Boolean).join(' · ')
    case 'parallel': return `for ${config.item_var || 'item'} in ${config.items || '…'}`
    case 'subflow': return config.loop_id || ''
    case 'checks': return Object.keys(config).filter((k) => config[k] && (Array.isArray(config[k]) ? config[k].length : true)).join(', ')
    case 'render': return `${config.source ?? ''} → ${config.output ?? ''}`
    case 'memory': return config.action === 'write' ? t('写入 {{scope}}', { scope: config.scope ?? '' }) : t('整理记忆')
    case 'note': return (config.text || '').slice(0, 80)
    default: return ''
  }
}

export function getPath(obj: Record<string, any>, path: string): any {
  return path.split('.').reduce<any>((o, k) => (o == null ? undefined : o[k]), obj)
}

export function setPath(obj: Record<string, any>, path: string, value: any): Record<string, any> {
  const keys = path.split('.')
  const copy = { ...obj }
  let cur: Record<string, any> = copy
  keys.forEach((k, i) => {
    if (i === keys.length - 1) {
      if (value === '' || value === undefined || (Array.isArray(value) && value.length === 0)) delete cur[k]
      else cur[k] = value
    } else {
      cur[k] = { ...(cur[k] ?? {}) }
      cur = cur[k]
    }
  })
  if (keys.length > 1 && Object.keys(copy[keys[0]] ?? {}).length === 0) delete copy[keys[0]]
  return copy
}
