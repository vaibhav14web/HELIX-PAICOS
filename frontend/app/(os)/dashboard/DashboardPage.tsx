'use client'

import { motion } from 'framer-motion'
import { GlassCard, MetricCard, StatusBadge } from '@/components/os/GlassCard'
import { useOSStore } from '@/lib/os-store'
import { useHealth, useSystemProfile, useSystemStats, useSystemState } from '@/lib/hooks/use-system'
import { useSystemMetrics } from '@/lib/hooks/use-system-metrics'
import { useAggregatedContext } from '@/lib/hooks/use-context'
import { useProductivity } from '@/lib/hooks/use-productivity'
import { usePermissions } from '@/lib/hooks/use-permissions'
import {
  Cpu, MemoryStick, HardDrive, Wifi, Bot, Zap,
  MessageSquare, GitBranch, Clock, ArrowRight,
  Thermometer, Shield, Database, Globe, Workflow,
  Plus, Check, Play
} from 'lucide-react'

const quickActions = [
  { label: 'New Chat', icon: MessageSquare, section: 'chat' as const, color: 'text-[#00d4ff]', bg: 'bg-[rgba(0,212,255,0.1)]' },
  { label: 'Voice Input', icon: Zap, section: 'voice' as const, color: 'text-[#7c3aed]', bg: 'bg-[rgba(124,58,237,0.1)]' },
  { label: 'Deploy Agent', icon: Bot, section: 'agents' as const, color: 'text-[#22c55e]', bg: 'bg-[rgba(34,197,94,0.1)]' },
  { label: 'Memory', icon: GitBranch, section: 'memory' as const, color: 'text-[#f59e0b]', bg: 'bg-[rgba(245,158,11,0.1)]' },
]

const item = { hidden: { opacity: 0, y: 10 }, show: { opacity: 1, y: 0, transition: { duration: 0.3 } } }

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 GB'
  const gb = bytes / (1024 ** 3)
  return `${gb.toFixed(1)} GB`
}

export function DashboardPage() {
  const setActiveSection = useOSStore((s) => s.setActiveSection)

  const { data: health, isLoading: healthLoading } = useHealth()
  const { data: profile } = useSystemProfile()
  const { data: stats } = useSystemStats()
  const { data: stateData } = useSystemState()
  const { data: metrics, isLoading: metricsLoading } = useSystemMetrics()
  const { data: contextData } = useAggregatedContext()
  const { patterns, suggestions } = useProductivity()

  const browserContext = (contextData?.context?.browser as any) || {}
  const browserTabs = (browserContext?.tabs as any[]) || []

  const greeting = (() => {
    const h = new Date().getHours()
    if (h < 12) return 'Good morning'
    if (h < 18) return 'Good afternoon'
    return 'Good evening'
  })()

  const username = profile?.username ?? 'Commander'
  const state = stateData?.state ?? health?.state ?? 'unknown'
  const stateStatus: 'online' | 'busy' | 'idle' = state === 'processing' || state === 'responding' ? 'busy' : state === 'idle' || state === 'listening' ? 'online' : 'idle'

  return (
    <motion.div
      initial="hidden"
      animate="show"
      className="p-6 space-y-6 max-w-[1400px] mx-auto"
    >
      <motion.div variants={item} className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-zinc-100 tracking-tight">
            {greeting}, <span className="text-gradient-cyan">{username}</span>
          </h1>
          <p className="text-sm text-zinc-500 mt-1">
            {healthLoading || metricsLoading ? 'Connecting...' : 'HELIX Platform is running'}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={stateStatus} label={`State: ${state}`} />
          <div className="text-xs font-mono text-zinc-600 flex items-center gap-1.5">
            <Clock className="w-3 h-3" />
            <span>Uptime: 14d 06h 22m</span>
          </div>
        </div>
      </motion.div>

      <motion.div variants={item} className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {quickActions.map((qa) => {
          const Icon = qa.icon
          return (
            <button
              key={qa.section}
              onClick={() => setActiveSection(qa.section)}
              className="glass rounded-xl p-4 flex items-center gap-3 hover:border-[rgba(0,212,255,0.15)] transition-all group"
            >
              <div className={`p-2 rounded-lg ${qa.bg}`}>
                <Icon className={`w-4 h-4 ${qa.color}`} />
              </div>
              <span className="text-sm font-medium text-zinc-300 group-hover:text-zinc-100 transition-colors">{qa.label}</span>
              <ArrowRight className="w-3 h-3 text-zinc-700 ml-auto group-hover:text-zinc-400 transition-colors" />
            </button>
          )
        })}
      </motion.div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <motion.div variants={item} className="lg:col-span-2 space-y-4">
          <h2 className="text-xs font-mono uppercase tracking-widest text-zinc-600">System Telemetry</h2>
          <div className="grid grid-cols-2 gap-3">
            <MetricCard
              label="CPU"
              value={metrics?.cpu_percent ?? '—'}
              unit="%"
              sub={metrics?.gpu?.available ? `GPU: ${metrics.gpu.utilization_percent ?? '?'}%` : 'No GPU data'}
              color="cyan"
              icon={<Cpu className="w-3.5 h-3.5" />}
            />
            <MetricCard
              label="RAM"
              value={metrics?.memory?.percent ?? '—'}
              unit="%"
              sub={metrics?.memory ? `${formatBytes(metrics.memory.used)} / ${formatBytes(metrics.memory.total)}` : '—'}
              color="purple"
              icon={<MemoryStick className="w-3.5 h-3.5" />}
            />
            <MetricCard
              label="Storage"
              value={metrics?.disk?.percent ?? '—'}
              unit="%"
              sub={metrics?.disk ? `${formatBytes(metrics.disk.used)} / ${formatBytes(metrics.disk.total)}` : '—'}
              color="orange"
              icon={<HardDrive className="w-3.5 h-3.5" />}
            />
            <MetricCard
              label="CPU Temp"
              value={metrics?.gpu?.temperature_c ?? '—'}
              unit="°C"
              sub={metrics?.gpu?.available ? 'GPU sensor' : 'No GPU sensor'}
              color={((metrics?.gpu?.temperature_c ?? 0) > 75) ? 'red' : 'green'}
              icon={<Thermometer className="w-3.5 h-3.5" />}
            />
          </div>

          <div className="grid grid-cols-3 gap-3">
            <MetricCard
              label="Network"
              value={(metrics as any)?.network?.bytes_recv ? formatBytes((metrics as any).network.bytes_recv) : 'Online'}
              sub={(metrics as any)?.network ? `▲ ${formatBytes((metrics as any).network.bytes_sent)} sent` : '127.0.0.1 link'}
              color="cyan"
              icon={<Wifi className="w-3.5 h-3.5" />}
            />
            <MetricCard
              label="Active Agents"
              value="3 Active"
              sub="Coding, Learn, Auto"
              color="green"
              icon={<Bot className="w-3.5 h-3.5" />}
            />
            <MetricCard
              label="System State"
              value={state}
              sub={health ? `${health.modules.length} modules online` : 'Idle'}
              color="purple"
              icon={<Database className="w-3.5 h-3.5" />}
            />
          </div>
        </motion.div>

        <motion.div variants={item} className="space-y-4">
          <h2 className="text-xs font-mono uppercase tracking-widest text-zinc-600">Intelligence Status</h2>
          <GlassCard className="space-y-3">
            {healthLoading ? (
              <div className="text-xs text-zinc-600 font-mono">Loading modules...</div>
            ) : (
              (health?.modules ?? []).slice(0, 6).map((mod) => (
                <div key={mod} className="flex items-center justify-between py-1 border-b border-[rgba(255,255,255,0.04)] last:border-0">
                  <div>
                    <div className="text-xs text-zinc-400 capitalize">{mod.replace(/_/g, ' ')}</div>
                    <div className="text-xs font-mono text-zinc-600 mt-0.5">Initialized</div>
                  </div>
                  <StatusBadge status="online" />
                </div>
              ))
            )}
          </GlassCard>

          <GlassCard className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-[rgba(34,197,94,0.1)]">
              <Shield className="w-5 h-5 text-[#22c55e]" />
            </div>
            <div>
              <div className="text-sm font-medium text-zinc-200">Security: Nominal</div>
              <div className="text-xs text-zinc-600 font-mono">E2E encrypted</div>
            </div>
          </GlassCard>

          <GlassCard className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-[rgba(124,58,237,0.1)]">
              <Database className="w-5 h-5 text-[#7c3aed]" />
            </div>
            <div className="flex-1">
              <div className="flex items-center justify-between">
                <div className="text-sm font-medium text-zinc-200">Backend</div>
                <span className="text-xs font-mono text-[#7c3aed]">{health?.status ?? '—'}</span>
              </div>
              <div className="w-full h-1 bg-[rgba(255,255,255,0.06)] rounded-full mt-2">
                <div className="h-full w-[74%] bg-[#7c3aed] rounded-full" />
              </div>
              <div className="text-xs text-zinc-600 font-mono mt-1">API v2.0.0</div>
            </div>
          </GlassCard>
        </motion.div>
      </div>

      <motion.div variants={item} className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <GlassCard className="text-center">
          <div className="text-2xl font-bold font-mono text-[#00d4ff]">{stats?.conversations ?? 0}</div>
          <div className="text-xs text-zinc-500 mt-1">Conversations</div>
        </GlassCard>
        <GlassCard className="text-center">
          <div className="text-2xl font-bold font-mono text-[#7c3aed]">{stats?.preferences ?? 0}</div>
          <div className="text-xs text-zinc-500 mt-1">Preferences</div>
        </GlassCard>
        <GlassCard className="text-center">
          <div className="text-2xl font-bold font-mono text-[#22c55e]">{stats?.explanations ?? 0}</div>
          <div className="text-xs text-zinc-500 mt-1">Explanations</div>
        </GlassCard>
        <GlassCard className="text-center">
          <div className="text-2xl font-bold font-mono text-[#f59e0b]">{stats?.pending_permissions ?? 0}</div>
          <div className="text-xs text-zinc-500 mt-1">Pending Permissions</div>
        </GlassCard>
      </motion.div>

      {/* Workspace Widgets System (Gap 3.3) */}
      <motion.div variants={item} className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Browser Widget */}
        <GlassCard className="flex flex-col gap-3 h-[240px]">
          <div className="flex items-center gap-2 border-b border-[rgba(255,255,255,0.04)] pb-2">
            <div className="p-1.5 rounded bg-[rgba(0,212,255,0.08)]">
              <Globe className="w-4 h-4 text-[#00d4ff]" />
            </div>
            <div>
              <h3 className="text-xs font-bold font-mono uppercase tracking-wider text-zinc-200">
                Browser Widget
              </h3>
              <p className="text-[9px] font-mono text-zinc-500 mt-0.5">
                ACTIVE BROWSER CONTEXT
              </p>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto space-y-2.5">
            {browserTabs && browserTabs.length > 0 ? (
              <div className="space-y-1.5">
                <div className="text-[10px] text-zinc-500 font-mono">
                  {browserTabs.length} open tabs:
                </div>
                {browserTabs.slice(0, 3).map((tab: any, idx: number) => (
                  <div key={idx} className="flex items-center justify-between text-xs p-1.5 rounded bg-[rgba(255,255,255,0.02)] border border-[rgba(255,255,255,0.04)]">
                    <span className="text-zinc-300 truncate max-w-[200px]" title={tab.title}>
                      {tab.title}
                    </span>
                    <span className="text-[9px] font-mono text-zinc-600 shrink-0">
                      {tab.browser || 'Tab'}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-full text-center py-4">
                <Globe className="w-8 h-8 text-zinc-700 mb-1.5" />
                <p className="text-xs text-zinc-500">No active browser tabs found</p>
                <p className="text-[10px] text-zinc-600 font-mono mt-0.5">Start a browser process to link context</p>
              </div>
            )}
          </div>
        </GlassCard>

        {/* Memory Widget */}
        <GlassCard className="flex flex-col gap-3 h-[240px]">
          <div className="flex items-center gap-2 border-b border-[rgba(255,255,255,0.04)] pb-2">
            <div className="p-1.5 rounded bg-[rgba(124,58,237,0.08)]">
              <Database className="w-4 h-4 text-[#7c3aed]" />
            </div>
            <div>
              <h3 className="text-xs font-bold font-mono uppercase tracking-wider text-zinc-200">
                Memory Widget
              </h3>
              <p className="text-[9px] font-mono text-zinc-500 mt-0.5">
                COGNITIVE PROFILE STATUS
              </p>
            </div>
          </div>
          <div className="flex-1 space-y-3 text-xs overflow-y-auto">
            <div className="flex items-center justify-between py-1 border-b border-[rgba(255,255,255,0.03)]">
              <span className="text-zinc-400">Memory Scope</span>
              <span className="font-mono text-[#7c3aed]">7-Day Rolling Log</span>
            </div>
            <div className="flex items-center justify-between py-1 border-b border-[rgba(255,255,255,0.03)]">
              <span className="text-zinc-400">Total Preferences Cached</span>
              <span className="font-mono text-zinc-200">{stats?.preferences ?? 0}</span>
            </div>
            <div className="flex items-center justify-between py-1 border-b border-[rgba(255,255,255,0.03)]">
              <span className="text-zinc-400">Historical Explanations</span>
              <span className="font-mono text-[#22c55e]">{stats?.explanations ?? 0}</span>
            </div>
            <div className="flex items-center justify-between py-1">
              <span className="text-zinc-400">Storage Optimization</span>
              <span className="font-mono text-zinc-500">Nominal</span>
            </div>
          </div>
        </GlassCard>

        {/* Productivity Widget */}
        <GlassCard className="flex flex-col gap-3 h-[240px]">
          <div className="flex items-center gap-2 border-b border-[rgba(255,255,255,0.04)] pb-2">
            <div className="p-1.5 rounded bg-[rgba(34,197,94,0.08)]">
              <Workflow className="w-4 h-4 text-[#22c55e]" />
            </div>
            <div>
              <h3 className="text-xs font-bold font-mono uppercase tracking-wider text-zinc-200">
                Productivity Widget
              </h3>
              <p className="text-[9px] font-mono text-zinc-500 mt-0.5">
                OBSERVED PATTERNS & SUGGESTIONS
              </p>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto space-y-2">
            {suggestions.data?.suggestions && suggestions.data.suggestions.length > 0 ? (
              <div className="space-y-2">
                {suggestions.data.suggestions.slice(0, 2).map((s: any) => (
                  <div key={s.suggestion_id} className="p-2 rounded-lg bg-[rgba(255,255,255,0.02)] border border-[rgba(255,255,255,0.04)] space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-zinc-200 truncate">{s.title}</span>
                      <span className="text-[9px] bg-[rgba(34,197,94,0.1)] border border-[rgba(34,197,94,0.2)] text-[#22c55e] px-1 rounded font-mono shrink-0">
                        Suggest
                      </span>
                    </div>
                    <p className="text-[10px] text-zinc-500 leading-normal">{s.description}</p>
                  </div>
                ))}
              </div>
            ) : patterns.data?.patterns && patterns.data.patterns.length > 0 ? (
              <div className="space-y-1.5">
                <div className="text-[10px] text-zinc-500 font-mono">
                  {patterns.data.patterns.length} patterns observed:
                </div>
                {patterns.data.patterns.slice(0, 3).map((p: any) => (
                  <div key={p.action} className="flex items-center justify-between text-xs py-1 px-1.5 rounded bg-[rgba(255,255,255,0.01)]">
                    <span className="text-zinc-400 font-mono text-[10px] truncate max-w-[150px]">
                      {p.action}
                    </span>
                    <span className="text-[10px] font-mono text-[#22c55e]">
                      freq: {p.frequency}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-full text-center py-4">
                <Workflow className="w-8 h-8 text-zinc-700 mb-1.5" />
                <p className="text-xs text-zinc-500">Telemetry collecting patterns</p>
                <p className="text-[10px] text-zinc-600 font-mono mt-0.5">Automation opportunities show up here</p>
              </div>
            )}
          </div>
        </GlassCard>
      </motion.div>
    </motion.div>
  )
}
