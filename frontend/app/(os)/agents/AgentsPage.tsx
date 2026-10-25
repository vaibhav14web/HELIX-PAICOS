'use client'

import { useState } from 'react'
import { motion } from 'framer-motion'
import { GlassCard } from '@/components/os/GlassCard'
import { StatusBadge, ProgressRing } from '@/components/os/GlassCard'
import { Bot, Play, Pause, Square, Plus, ChevronRight, Zap, Clock, BarChart3, Terminal, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { usePermissions } from '@/lib/hooks/use-permissions'
import { useWork } from '@/lib/hooks/use-work'
import { useSystemStats, useSystemState } from '@/lib/hooks/use-system'
import { api } from '@/lib/api-client'

type AgentStatus = 'online' | 'busy' | 'idle' | 'offline' | 'error'

interface Agent {
  id: string
  name: string
  description: string
  status: AgentStatus
  model: string
  progress?: number
  task?: string
  tasksCompleted: number
  tokensUsed: string
  uptime: string
  color: string
  bgColor: string
}

const defaultAgents: Agent[] = [
  {
    id: '1', name: 'ResearchBot', description: 'Deep web research & synthesis',
    status: 'idle', model: 'llama.cpp/Qwen3-4B', progress: undefined,
    task: undefined,
    tasksCompleted: 0, tokensUsed: '0', uptime: '—',
    color: 'text-[#00d4ff]', bgColor: 'bg-[rgba(0,212,255,0.08)]',
  },
  {
    id: '2', name: 'DataBot', description: 'Data processing & memory indexing',
    status: 'idle', model: 'llama.cpp/Qwen3-4B', progress: undefined,
    task: undefined,
    tasksCompleted: 0, tokensUsed: '0', uptime: '—',
    color: 'text-[#7c3aed]', bgColor: 'bg-[rgba(124,58,237,0.08)]',
  },
  {
    id: '3', name: 'AutoBot', description: 'Automation pipeline executor',
    status: 'idle', model: 'llama.cpp/Qwen3-4B', progress: undefined,
    task: undefined,
    tasksCompleted: 0, tokensUsed: '0', uptime: '—',
    color: 'text-[#22c55e]', bgColor: 'bg-[rgba(34,197,94,0.08)]',
  },
  {
    id: '4', name: 'CodeBot', description: 'Code generation & review',
    status: 'idle', model: 'llama.cpp/qwen2.5-coder:3b', progress: undefined,
    task: undefined,
    tasksCompleted: 0, tokensUsed: '0', uptime: '—',
    color: 'text-[#f59e0b]', bgColor: 'bg-[rgba(245,158,11,0.08)]',
  },
  {
    id: '5', name: 'MonitorBot', description: 'System health monitoring',
    status: 'online', model: 'llama.cpp/Qwen3-4B', progress: undefined,
    task: 'Waiting for data...',
    tasksCompleted: 0, tokensUsed: '0', uptime: '—',
    color: 'text-[#ef4444]', bgColor: 'bg-[rgba(239,68,68,0.08)]',
  },
  {
    id: '6', name: 'SchedulerBot', description: 'Task scheduling & queue management',
    status: 'idle', model: 'llama.cpp/Qwen3-4B', progress: undefined,
    task: undefined,
    tasksCompleted: 0, tokensUsed: '0', uptime: '—',
    color: 'text-zinc-500', bgColor: 'bg-[rgba(255,255,255,0.04)]',
  },
]

const logs: { agent: string; msg: string; time: string; color: string }[] = []

const container = { hidden: {}, show: { transition: { staggerChildren: 0.07 } } }
const item = { hidden: { opacity: 0, y: 8 }, show: { opacity: 1, y: 0, transition: { duration: 0.25 } } }

export function AgentsPage() {
  const { data: pendingPerms } = usePermissions().pending
  const { data: systemStats } = useSystemStats()
  const { data: stateData } = useSystemState()
  const { tasks } = useWork()

  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(defaultAgents[0])
  const [filter, setFilter] = useState<'all' | AgentStatus>('all')

  const tasksList = tasks.data?.tasks ?? []
  const pendingCount = pendingPerms?.pending?.length ?? 0
  const state = stateData?.state ?? 'unknown'

  const agents = defaultAgents.map((a) => {
    if (a.name === 'MonitorBot' && state) {
      return { ...a, task: `System state: ${state}`, status: state === 'idle' ? 'online' as const : 'busy' as const }
    }
    return a
  })

  const filtered = filter === 'all' ? agents : agents.filter((a) => a.status === filter)

  const handleDeployAgent = async () => {
    const name = window.prompt('Enter new agent name:')
    if (!name?.trim()) return
    const role = window.prompt('Enter agent role/description:') || 'Custom Subagent'
    try {
      await api.agents.deploy({ name: name.trim(), role: role.trim() })
      alert(`Agent '${name}' deployed successfully!`)
    } catch (e) {
      console.warn('Failed to deploy agent', e)
    }
  }

  return (
    <div className="flex h-full">
      {/* Agent list */}
      <div className="w-80 shrink-0 border-r border-[rgba(255,255,255,0.06)] flex flex-col">
        <div className="p-4 border-b border-[rgba(255,255,255,0.06)]">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-zinc-200">Agent Network</h2>
            <button onClick={handleDeployAgent} className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[rgba(0,212,255,0.08)] border border-[rgba(0,212,255,0.2)] text-[#00d4ff] text-xs font-medium hover:bg-[rgba(0,212,255,0.12)] transition-colors">
              <Plus className="w-3 h-3" />
              Deploy
            </button>
          </div>
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs text-zinc-600 font-mono">
              {pendingCount > 0 && `${pendingCount} pending permissions`}
            </span>
          </div>
          <div className="flex gap-1">
            {(['all', 'busy', 'online', 'idle', 'offline'] as const).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={cn('px-2 py-1 rounded text-xs capitalize transition-colors', filter === f ? 'bg-[rgba(0,212,255,0.1)] text-[#00d4ff]' : 'text-zinc-600 hover:text-zinc-300')}
              >
                {f}
              </button>
            ))}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto py-2">
          {filtered.map((agent) => (
            <button
              key={agent.id}
              onClick={() => setSelectedAgent(agent)}
              className={cn(
                'w-full flex items-start gap-3 px-4 py-3 transition-colors hover:bg-[rgba(255,255,255,0.03)] text-left',
                selectedAgent?.id === agent.id && 'bg-[rgba(0,212,255,0.05)] border-r-2 border-[#00d4ff]',
              )}
            >
              <div className={cn('p-2 rounded-lg shrink-0 mt-0.5', agent.bgColor)}>
                <Bot className={cn('w-4 h-4', agent.color)} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-zinc-200 truncate">{agent.name}</span>
                  <StatusBadge status={agent.status} />
                </div>
                <div className="text-xs text-zinc-600 truncate mt-0.5">{agent.description}</div>
                {agent.progress !== undefined && (
                  <div className="w-full h-1 bg-[rgba(255,255,255,0.06)] rounded-full mt-2">
                    <motion.div
                      className="h-full rounded-full"
                      style={{ background: agent.color.replace('text-[', '').replace(']', '') }}
                      animate={{ width: `${agent.progress}%` }}
                      transition={{ duration: 1 }}
                    />
                  </div>
                )}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Detail + logs */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {selectedAgent ? (
          <>
            <div className="p-6 border-b border-[rgba(255,255,255,0.06)]">
              <motion.div variants={container} initial="hidden" animate="show" className="space-y-4">
                <motion.div variants={item} className="flex items-start justify-between gap-4">
                  <div className="flex items-center gap-4">
                    <div className={cn('p-3 rounded-xl', selectedAgent.bgColor)}>
                      <Bot className={cn('w-7 h-7', selectedAgent.color)} />
                    </div>
                    <div>
                      <h2 className="text-xl font-bold text-zinc-100">{selectedAgent.name}</h2>
                      <p className="text-sm text-zinc-500">{selectedAgent.description}</p>
                      <div className="flex items-center gap-2 mt-1">
                        <StatusBadge status={selectedAgent.status} />
                        <span className="text-xs font-mono text-zinc-600">{selectedAgent.model}</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {[
                      { Icon: Play, label: 'Run', disabled: selectedAgent.status === 'busy' },
                      { Icon: Pause, label: 'Pause', disabled: selectedAgent.status === 'idle' || selectedAgent.status === 'offline' },
                      { Icon: Square, label: 'Stop', disabled: selectedAgent.status === 'offline' },
                      { Icon: RefreshCw, label: 'Restart', disabled: false },
                    ].map(({ Icon, label, disabled }) => (
                      <button
                        key={label}
                        disabled={disabled}
                        title={label}
                        className="p-2 rounded-lg glass text-zinc-500 hover:text-zinc-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      >
                        <Icon className="w-4 h-4" />
                      </button>
                    ))}
                  </div>
                </motion.div>

                {selectedAgent.task && (
                  <motion.div variants={item} className="flex items-start gap-3 p-3 rounded-xl bg-[rgba(0,212,255,0.05)] border border-[rgba(0,212,255,0.1)]">
                    <Zap className="w-4 h-4 text-[#00d4ff] mt-0.5 shrink-0 animate-pulse-glow" />
                    <div>
                      <div className="text-xs font-mono text-zinc-500 mb-0.5">Current Task</div>
                      <div className="text-sm text-zinc-300">{selectedAgent.task}</div>
                      {selectedAgent.progress !== undefined && (
                        <div className="flex items-center gap-2 mt-2">
                          <div className="flex-1 h-1.5 bg-[rgba(255,255,255,0.06)] rounded-full overflow-hidden">
                            <motion.div className="h-full bg-[#00d4ff] rounded-full" animate={{ width: `${selectedAgent.progress}%` }} />
                          </div>
                          <span className="text-xs font-mono text-[#00d4ff]">{selectedAgent.progress}%</span>
                        </div>
                      )}
                    </div>
                  </motion.div>
                )}

                <motion.div variants={item} className="grid grid-cols-3 gap-3">
                  {[
                    { icon: BarChart3, label: 'Tasks Done', value: selectedAgent.tasksCompleted.toLocaleString(), color: 'text-[#00d4ff]' },
                    { icon: Zap, label: 'Tokens Used', value: selectedAgent.tokensUsed, color: 'text-[#7c3aed]' },
                    { icon: Clock, label: 'Uptime', value: selectedAgent.uptime, color: 'text-[#22c55e]' },
                  ].map(({ icon: Icon, label, value, color }) => (
                    <GlassCard key={label} className="flex items-center gap-3">
                      <Icon className={cn('w-4 h-4 shrink-0', color)} />
                      <div>
                        <div className={cn('text-sm font-bold font-mono', color)}>{value}</div>
                        <div className="text-[10px] text-zinc-600 mt-0.5">{label}</div>
                      </div>
                    </GlassCard>
                  ))}
                </motion.div>

                {/* Pending tasks from API */}
                {tasksList.length > 0 && (
                  <motion.div variants={item}>
                    <h4 className="text-xs font-mono uppercase tracking-widest text-zinc-600 mb-2">Open Tasks</h4>
                    <div className="space-y-1">
                      {tasksList.map((task, i) => (
                        <div key={i} className="flex items-center gap-2 text-xs text-zinc-400 px-2 py-1">
                          <div className="w-1 h-1 rounded-full bg-[#f59e0b]" />
                          {task}
                        </div>
                      ))}
                    </div>
                  </motion.div>
                )}
              </motion.div>
            </div>

            <div className="flex-1 overflow-y-auto p-6">
              <div className="flex items-center gap-2 mb-4">
                <Terminal className="w-4 h-4 text-zinc-600" />
                <h3 className="text-xs font-mono uppercase tracking-widest text-zinc-600">Agent Logs</h3>
                <div className="w-1.5 h-1.5 rounded-full bg-[#22c55e] animate-pulse-glow ml-1" />
              </div>
              <div className="space-y-2 font-mono text-xs">
                {logs.map((log, i) => (
                  <motion.div key={i} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.08 }} className="flex items-start gap-3">
                    <span className="text-zinc-700 shrink-0">[{log.time}]</span>
                    <span className={cn('shrink-0', log.color)}>{log.agent}:</span>
                    <span className="text-zinc-400">{log.msg}</span>
                  </motion.div>
                ))}
              </div>
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-zinc-600">
            <div className="text-center space-y-2">
              <Bot className="w-12 h-12 mx-auto opacity-30" />
              <p className="text-sm">Select an agent to view details</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
