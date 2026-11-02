import { Background, Controls, Handle, MarkerType, MiniMap, NodeResizer, Position, ReactFlow, ReactFlowProvider,
  applyNodeChanges, useReactFlow, type Connection, type Edge, type Node, type NodeChange, type NodeProps } from '@xyflow/react'
import { AlertTriangle, CheckCircle2, CircleDashed, Hourglass, Loader2, XCircle } from 'lucide-react'
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Issue, LoopGraph, LoopNode } from '@/lib/types'
import { cn, toast, uid } from '@/lib/utils'
import { NODE_META, summarize } from './meta'

export interface NodeStatus {
  status: string
  iterations?: number
  error?: string | null
}

interface CanvasCtx {
  editable: boolean
  onResize: (id: string, box: { x: number; y: number; width: number; height: number }) => void
}
const Ctx = createContext<CanvasCtx>({ editable: false, onResize: () => undefined })

interface NodeData extends Record<string, unknown> {
  node: LoopNode
  status?: NodeStatus
  issue?: string
}

function StatusIcon({ status }: { status?: string }) {
  if (!status) return null
  if (status === 'running') return <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
  if (status === 'done') return <CheckCircle2 className="h-3.5 w-3.5 text-success" />
  if (status === 'failed') return <XCircle className="h-3.5 w-3.5 text-danger" />
  if (status === 'waiting') return <Hourglass className="h-3.5 w-3.5 animate-pulse text-warning" />
  return <CircleDashed className="h-3.5 w-3.5 text-subtle" />
}

function statusRing(status?: string): string {
  return status === 'running' ? 'a4s-running border-primary' : status === 'failed' ? 'border-danger' :
    status === 'waiting' ? 'border-warning' : status === 'done' ? 'border-success/60' : ''
}

function StepNode({ data, selected }: NodeProps<Node<NodeData>>) {
  const { t } = useTranslation()
  const { node, status, issue } = data
  const meta = NODE_META[node.type] ?? NODE_META.agent
  const Icon = meta.icon
  if (node.type === 'start' || node.type === 'end') {
    return (
      <div className={cn('flex items-center gap-1.5 rounded-full border bg-surface px-3 py-1 text-xs font-medium shadow-sm',
        selected ? 'border-primary ring-2 ring-primary/30' : 'border-border', statusRing(status?.status))}>
        {node.type === 'end' && <Handle type="target" position={Position.Top} />}
        <Icon className="h-3 w-3 text-muted" />{t(node.label || meta.label)}
        {node.type === 'start' && <Handle type="source" position={Position.Bottom} />}
      </div>
    )
  }
  if (node.type === 'note') {
    return (
      <div className={cn('w-56 rounded-md border border-warning/40 bg-warning/10 p-2 text-[11px] leading-relaxed whitespace-pre-wrap text-muted shadow-sm',
        selected && 'ring-2 ring-primary/30')}>{node.config.text || t('注释')}</div>
    )
  }
  const summary = summarize(node.type, node.config, t)
  return (
    <div className={cn('w-60 rounded-md border bg-surface shadow-sm transition', selected ? 'border-primary ring-2 ring-primary/30' : 'border-border',
      statusRing(status?.status), issue && 'border-danger ring-2 ring-danger/25')}
      style={{ borderLeft: `4px solid ${meta.color}` }} title={issue || status?.error || undefined}>
      <Handle type="target" position={Position.Top} />
      <div className="flex items-center gap-2 px-2.5 pt-2">
        <Icon className="h-3.5 w-3.5 shrink-0" style={{ color: meta.color }} />
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{t(node.label || meta.label)}</span>
        {issue ? <AlertTriangle className="h-3.5 w-3.5 text-danger" /> : <StatusIcon status={status?.status} />}
      </div>
      <div className="truncate px-2.5 pt-0.5 pb-2 text-[11px] text-muted">{summary || t(meta.label)}</div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
}

function ContainerNode({ id, data, selected }: NodeProps<Node<NodeData>>) {
  const { t } = useTranslation()
  const { editable, onResize } = useContext(Ctx)
  const { node, status, issue } = data
  const meta = NODE_META[node.type]
  const Icon = meta.icon
  return (
    <div className={cn('h-full w-full rounded-lg border-2 border-dashed transition', selected && 'ring-2 ring-primary/30',
      status?.status === 'running' && 'a4s-running', issue && 'border-danger')}
      style={{ borderColor: issue ? undefined : `color-mix(in oklab, ${meta.color} 55%, transparent)`,
        background: `color-mix(in oklab, ${meta.color} 5%, transparent)` }}>
      {editable && <NodeResizer isVisible={selected} minWidth={280} minHeight={160} lineClassName="!border-primary"
        handleClassName="!h-2 !w-2 !border-primary !bg-surface" onResizeEnd={(_, p) => onResize(id, p)} />}
      <Handle type="target" position={Position.Top} />
      <div className="flex items-center gap-2 px-3 py-2">
        <Icon className="h-3.5 w-3.5 shrink-0" style={{ color: meta.color }} />
        <span className="truncate text-[13px] font-semibold">{t(node.label || meta.label)}</span>
        {status?.iterations ? <span className="rounded-full bg-surface px-1.5 text-[10px] font-medium text-muted">{t('第 {{n}} 轮', { n: status.iterations })}</span> : null}
        <span className="min-w-0 flex-1 truncate text-[11px] text-muted">{summarize(node.type, node.config, t)}</span>
        {issue ? <AlertTriangle className="h-3.5 w-3.5 text-danger" /> : <StatusIcon status={status?.status} />}
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
}

const nodeTypes = { step: StepNode, container: ContainerNode }

function absolutePosition(graph: LoopGraph, id: string): { x: number; y: number } {
  const node = graph.nodes.find((n) => n.id === id)
  if (!node) return { x: 0, y: 0 }
  if (!node.parent) return node.position
  const parent = absolutePosition(graph, node.parent)
  return { x: parent.x + node.position.x, y: parent.y + node.position.y }
}

function toFlowNodes(graph: LoopGraph, statuses?: Record<string, NodeStatus>, issues?: Issue[]): Node<NodeData>[] {
  const depth = (n: LoopNode): number => (n.parent ? 1 + depth(graph.nodes.find((p) => p.id === n.parent) ?? { ...n, parent: null }) : 0)
  const ordered = [...graph.nodes].sort((a, b) => depth(a) - depth(b) || Number(!!NODE_META[b.type]?.container) - Number(!!NODE_META[a.type]?.container))
  const issueFor = (id: string) => issues?.find((i) => i.node === id && i.level === 'error')?.message
  return ordered.map((n) => {
    const container = !!NODE_META[n.type]?.container
    return {
      id: n.id,
      type: container ? 'container' : 'step',
      position: n.position,
      parentId: n.parent ?? undefined,
      extent: n.parent ? ('parent' as const) : undefined,
      data: { node: n, status: statuses?.[n.id], issue: issueFor(n.id) },
      style: container ? { width: n.width ?? 380, height: n.height ?? 260 } : undefined,
      zIndex: container ? 0 : 1,
    }
  })
}

function toFlowEdges(graph: LoopGraph, statuses?: Record<string, NodeStatus>): Edge[] {
  return graph.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    label: e.label || (e.condition ? (e.condition.length > 28 ? e.condition.slice(0, 28) + '…' : e.condition) : undefined),
    animated: statuses?.[e.target]?.status === 'running',
    style: e.condition ? { strokeDasharray: '5 4' } : undefined,
    markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
    zIndex: 2,
  }))
}

export interface LoopCanvasProps {
  graph: LoopGraph
  editable?: boolean
  statuses?: Record<string, NodeStatus>
  issues?: Issue[]
  onChange?: (graph: LoopGraph) => void
  onSelect?: (selection: { kind: 'node' | 'edge'; id: string } | null) => void
}

function Canvas({ graph, editable = false, statuses, issues, onChange, onSelect }: LoopCanvasProps) {
  const { t } = useTranslation()
  const flow = useReactFlow()
  const [nodes, setNodes] = useState<Node<NodeData>[]>(() => toFlowNodes(graph, statuses, issues))
  const edges = useMemo(() => toFlowEdges(graph, statuses), [graph, statuses])
  useEffect(() => setNodes(toFlowNodes(graph, statuses, issues)), [graph, statuses, issues])

  const commit = useCallback((next: LoopGraph) => onChange?.(next), [onChange])

  const onResize = useCallback((id: string, box: { x: number; y: number; width: number; height: number }) => {
    commit({ ...graph, nodes: graph.nodes.map((n) => (n.id === id ? { ...n, width: Math.round(box.width), height: Math.round(box.height),
      position: { x: Math.round(box.x), y: Math.round(box.y) } } : n)) })
  }, [graph, commit])

  const onNodesChange = useCallback((changes: NodeChange<Node<NodeData>>[]) => {
    setNodes((ns) => applyNodeChanges(changes, ns))
  }, [])

  const onConnect = useCallback((c: Connection) => {
    if (!editable || !c.source || !c.target || c.source === c.target) return
    const s = graph.nodes.find((n) => n.id === c.source)
    const tg = graph.nodes.find((n) => n.id === c.target)
    if ((s?.parent ?? null) !== (tg?.parent ?? null)) {
      toast.error(t('连线不能跨越容器边界：请连到容器本身'))
      return
    }
    if (graph.edges.some((e) => e.source === c.source && e.target === c.target)) return
    const condition = s?.type === 'condition' ? (graph.edges.some((e) => e.source === s.id && e.condition === 'true') ? 'false' : 'true') : null
    commit({ ...graph, edges: [...graph.edges, { id: uid('e_'), source: c.source, target: c.target, condition, label: '' }] })
  }, [editable, graph, commit, t])

  const onDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    const type = event.dataTransfer.getData('application/a4s-node')
    if (!type || !editable) return
    const point = flow.screenToFlowPosition({ x: event.clientX, y: event.clientY })
    const meta = NODE_META[type]
    let parent: string | null = null
    let best = Infinity
    for (const n of graph.nodes) {
      if (!NODE_META[n.type]?.container) continue
      const abs = absolutePosition(graph, n.id)
      const w = n.width ?? 380
      const h = n.height ?? 260
      if (point.x >= abs.x && point.x <= abs.x + w && point.y >= abs.y && point.y <= abs.y + h && w * h < best) {
        parent = n.id
        best = w * h
      }
    }
    const origin = parent ? absolutePosition(graph, parent) : { x: 0, y: 0 }
    const id = `${type}_${uid()}`.slice(0, 20)
    const node: LoopNode = {
      id, type, label: t(meta.label), parent,
      position: { x: Math.round(point.x - origin.x - 30), y: Math.round(point.y - origin.y - 15) },
      config: structuredClone(meta.defaults),
      ...(meta.container ? { width: 400, height: 260 } : {}),
    }
    commit({ ...graph, nodes: [...graph.nodes, node] })
    onSelect?.({ kind: 'node', id })
  }, [editable, flow, graph, commit, onSelect, t])

  return (
    <Ctx.Provider value={{ editable, onResize }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onConnect={onConnect}
        nodesDraggable={editable}
        nodesConnectable={editable}
        elementsSelectable
        deleteKeyCode={editable ? ['Backspace', 'Delete'] : null}
        onNodeDragStop={(_, __, dragged) => {
          if (!editable) return
          const moved = new Map(dragged.map((d) => [d.id, d.position]))
          commit({ ...graph, nodes: graph.nodes.map((n) => (moved.has(n.id) ? { ...n, position: {
            x: Math.round(moved.get(n.id)!.x), y: Math.round(moved.get(n.id)!.y) } } : n)) })
        }}
        onNodesDelete={(deleted) => {
          if (!editable) return
          const ids = new Set(deleted.map((d) => d.id))
          let grew = true
          while (grew) {
            grew = false
            for (const n of graph.nodes) if (n.parent && ids.has(n.parent) && !ids.has(n.id)) { ids.add(n.id); grew = true }
          }
          commit({ ...graph, nodes: graph.nodes.filter((n) => !ids.has(n.id)),
            edges: graph.edges.filter((e) => !ids.has(e.source) && !ids.has(e.target)) })
          onSelect?.(null)
        }}
        onEdgesDelete={(deleted) => {
          if (!editable) return
          const ids = new Set(deleted.map((d) => d.id))
          commit({ ...graph, edges: graph.edges.filter((e) => !ids.has(e.id)) })
        }}
        onNodeClick={(_, n) => onSelect?.({ kind: 'node', id: n.id })}
        onEdgeClick={(_, e) => onSelect?.({ kind: 'edge', id: e.id })}
        onPaneClick={() => onSelect?.(null)}
        onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move' }}
        onDrop={onDrop}
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 1.1 }}
        minZoom={0.2}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={18} size={1.2} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeColor={(n) => NODE_META[(n.data as NodeData).node.type]?.color ?? 'gray'} maskColor="color-mix(in oklab, var(--a4s-bg) 70%, transparent)" />
      </ReactFlow>
    </Ctx.Provider>
  )
}

export function LoopCanvas(props: LoopCanvasProps) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  )
}
