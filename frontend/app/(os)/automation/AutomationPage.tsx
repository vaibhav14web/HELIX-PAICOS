'use client'

import { useCallback, useState } from 'react'
import ReactFlow, {
  Background,
  Controls,
  addEdge,
  useNodesState,
  useEdgesState,
  type Connection,
  type Node,
  BackgroundVariant,
} from 'reactflow'
import 'reactflow/dist/style.css'
import { motion } from 'framer-motion'
import { Play, Plus, Clock, Zap, CheckCircle, AlertCircle, Workflow } from 'lucide-react'
import { GlassCard } from '@/components/os/GlassCard'
import { StatusBadge } from '@/components/os/GlassCard'
import { cn } from '@/lib/utils'
import { useWork } from '@/lib/hooks/use-work'
import { api } from '@/lib/api-client'
import { RemindersWidget } from '@/components/os/RemindersWidget'

type FlowNodeType = 'trigger' | 'action' | 'condition' | 'output'

interface FlowNodeData {
  label: string
  type: FlowNodeType
  description: string
  status?: 'active' | 'done' | 'error' | 'waiting'
}

const nodeStyle: Record<FlowNodeType, { bg: string; border: string; icon: string }> = {
  trigger: { bg: 'rgba(0,212,255,0.1)', border: 'rgba(0,212,255,0.4)', icon: '⚡' },
  action: { bg: 'rgba(124,58,237,0.1)', border: 'rgba(124,58,237,0.4)', icon: '▶' },
  condition: { bg: 'rgba(245,158,11,0.1)', border: 'rgba(245,158,11,0.4)', icon: '◆' },
  output: { bg: 'rgba(34,197,94,0.1)', border: 'rgba(34,197,94,0.4)', icon: '✓' },
}

const statusIcon: Record<string, React.ComponentType<{ className?: string }>> = {
  done: CheckCircle,
  error: AlertCircle,
  active: Zap,
}

function FlowNode({ data }: { data: FlowNodeData }) {
  const s = nodeStyle[data.type]
  const SI = data.status ? statusIcon[data.status] : undefined
  return (
    <div
      className="px-4 py-3 rounded-xl min-w-[140px] text-xs"
      style={{
        background: s.bg,
        border: `1px solid ${s.border}`,
        boxShadow: `0 0 12px ${s.border.replace('0.4', '0.1')}`,
      }}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="text-[10px] uppercase tracking-wider opacity-60 font-mono">{data.type}</span>
        {SI && <SI className={cn('w-3 h-3', data.status === 'done' ? 'text-[#22c55e]' : data.status === 'error' ? 'text-[#ef4444]' : 'text-[#00d4ff]')} />}
      </div>
      <div className="font-medium text-zinc-200 text-xs">{data.label}</div>
      {data.description && <div className="text-[10px] text-zinc-500 mt-1">{data.description}</div>}
    </div>
  )
}

const nodeTypes = { flow: FlowNode }

const initialNodes: Node<FlowNodeData>[] = [
  { id: '1', type: 'flow', position: { x: 100, y: 180 }, data: { label: 'Schedule Trigger', type: 'trigger', description: 'Every day at 06:00', status: 'done' } },
  { id: '2', type: 'flow', position: { x: 320, y: 100 }, data: { label: 'Fetch Emails', type: 'action', description: 'Gmail unread inbox', status: 'done' } },
  { id: '3', type: 'flow', position: { x: 320, y: 260 }, data: { label: 'Fetch Calendar', type: 'action', description: 'Today\'s events', status: 'done' } },
  { id: '4', type: 'flow', position: { x: 540, y: 180 }, data: { label: 'Has Priority Email?', type: 'condition', description: 'Check sender & subject', status: 'active' } },
  { id: '5', type: 'flow', position: { x: 740, y: 100 }, data: { label: 'Summarize with AI', type: 'action', description: 'Claude summarization', status: 'waiting' } },
  { id: '6', type: 'flow', position: { x: 740, y: 260 }, data: { label: 'Skip Summary', type: 'action', description: 'Use default template', status: 'waiting' } },
  { id: '7', type: 'flow', position: { x: 960, y: 180 }, data: { label: 'Send Digest', type: 'output', description: 'Email & Slack push', status: 'waiting' } },
]

const initialEdges = [
  { id: 'e1-2', source: '1', target: '2', animated: true, label: 'start' },
  { id: 'e1-3', source: '1', target: '3', animated: true },
  { id: 'e2-4', source: '2', target: '4' },
  { id: 'e3-4', source: '3', target: '4' },
  { id: 'e4-5', source: '4', target: '5', label: 'yes' },
  { id: 'e4-6', source: '4', target: '6', label: 'no' },
  { id: 'e5-7', source: '5', target: '7' },
  { id: 'e6-7', source: '6', target: '7' },
]

const defaultPipelines = [
  { name: 'Daily Digest', status: 'idle' as const, lastRun: 'Not yet run', runs: 0, nextRun: 'Not scheduled' },
  { name: 'Memory Indexer', status: 'idle' as const, lastRun: 'Not yet run', runs: 0, nextRun: 'Not scheduled' },
  { name: 'Agent Scheduler', status: 'idle' as const, lastRun: 'Not yet run', runs: 0, nextRun: 'Not scheduled' },
  { name: 'Backup Pipeline', status: 'idle' as const, lastRun: 'Not yet run', runs: 0, nextRun: 'Not scheduled' },
  { name: 'Alert Monitor', status: 'online' as const, lastRun: 'Listening...', runs: 0, nextRun: 'Continuous' },
]

export function AutomationPage() {
  const { tasks } = useWork()
  const [nodes, , onNodesChange] = useNodesState(initialNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges)
  const [selectedPipeline, setSelectedPipeline] = useState(0)

  const tasksList = tasks.data?.tasks ?? []

  const pipelines = defaultPipelines.map((p) => {
    if (p.name === 'Agent Scheduler' && tasksList.length > 0) {
      return { ...p, lastRun: `${tasksList.length} pending tasks`, runs: p.runs + tasksList.length }
    }
    return p
  })

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge({ ...params, animated: true }, eds)),
    [setEdges],
  )

  const [running, setRunning] = useState(false)

  const handleRunPipeline = async () => {
    const pipe = pipelines[selectedPipeline]
    if (!pipe) return
    setRunning(true)
    try {
      const res = await api.automation.runPipeline(pipe.name.toLowerCase().replace(/ /g, '_'))
      console.log('Pipeline run completed:', res)
    } catch (e) {
      console.warn('Failed to run pipeline', e)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="flex h-full">
      {/* Pipeline list */}
      <div className="w-64 shrink-0 border-r border-[rgba(255,255,255,0.06)] flex flex-col">
        <div className="p-4 border-b border-[rgba(255,255,255,0.06)]">
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-sm font-semibold text-zinc-200">Pipelines</h2>
            <button className="flex items-center gap-1 px-2 py-1 rounded-lg bg-[rgba(0,212,255,0.08)] border border-[rgba(0,212,255,0.2)] text-[#00d4ff] text-xs hover:bg-[rgba(0,212,255,0.12)] transition-colors">
              <Plus className="w-3 h-3" /> New
            </button>
          </div>
          <p className="text-xs text-zinc-600">{pipelines.filter(p => p.status === 'online').length} active</p>
          {tasksList.length > 0 && (
            <p className="text-xs text-[#f59e0b] font-mono mt-1">{tasksList.length} queued tasks</p>
          )}
        </div>
        <div className="flex-1 overflow-y-auto py-2">
          {pipelines.map((p, i) => (
            <button
              key={p.name}
              onClick={() => setSelectedPipeline(i)}
              className={cn(
                'w-full flex items-start gap-3 px-4 py-3 transition-colors hover:bg-[rgba(255,255,255,0.03)] text-left',
                selectedPipeline === i && 'bg-[rgba(0,212,255,0.05)] border-r-2 border-[#00d4ff]',
              )}
            >
              <div className="p-1.5 rounded-lg bg-[rgba(255,255,255,0.04)] mt-0.5">
                <Workflow className="w-3.5 h-3.5 text-zinc-500" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between">
                  <span className={cn('text-sm font-medium', selectedPipeline === i ? 'text-zinc-200' : 'text-zinc-400')}>{p.name}</span>
                  <StatusBadge status={p.status} />
                </div>
                <div className="text-[10px] text-zinc-700 font-mono mt-0.5">{p.lastRun}</div>
              </div>
            </button>
          ))}
        </div>
        <div className="p-3 border-t border-[rgba(255,255,255,0.06)]">
          <RemindersWidget />
        </div>
      </div>

      {/* Flow canvas */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="flex items-center justify-between px-4 py-3 border-b border-[rgba(255,255,255,0.06)] shrink-0">
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-semibold text-zinc-200">{pipelines[selectedPipeline].name}</h3>
            <StatusBadge status={pipelines[selectedPipeline].status} />
            <div className="flex items-center gap-1.5 text-xs text-zinc-600 font-mono">
              <Clock className="w-3 h-3" />
              {pipelines[selectedPipeline].nextRun}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-zinc-600 font-mono">{pipelines[selectedPipeline].runs.toLocaleString()} runs</span>
            <button
              onClick={handleRunPipeline}
              disabled={running}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[rgba(34,197,94,0.1)] border border-[rgba(34,197,94,0.2)] text-[#22c55e] text-xs font-medium hover:bg-[rgba(34,197,94,0.15)] transition-colors disabled:opacity-50"
            >
              <Play className="w-3 h-3" /> {running ? 'Running...' : 'Run Now'}
            </button>
          </div>
        </div>

        <div className="flex items-center gap-4 px-4 py-2 border-b border-[rgba(255,255,255,0.06)] shrink-0">
          {Object.entries(nodeStyle).map(([type, cfg]) => (
            <div key={type} className="flex items-center gap-1.5">
              <div className="w-2 h-2 rounded-sm" style={{ background: cfg.bg, border: `1px solid ${cfg.border}` }} />
              <span className="text-[10px] text-zinc-600 capitalize font-mono">{type}</span>
            </div>
          ))}
          <div className="ml-auto flex items-center gap-3">
            {[
              { icon: CheckCircle, label: 'Done', color: 'text-[#22c55e]' },
              { icon: Zap, label: 'Active', color: 'text-[#00d4ff]' },
              { icon: AlertCircle, label: 'Error', color: 'text-[#ef4444]' },
            ].map(({ icon: Icon, label, color }) => (
              <div key={label} className="flex items-center gap-1">
                <Icon className={cn('w-3 h-3', color)} />
                <span className="text-[10px] text-zinc-600">{label}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="flex-1 min-h-0">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.3 }}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgba(255,255,255,0.025)" />
            <Controls position="top-right" />
          </ReactFlow>
        </div>
      </div>
    </div>
  )
}
