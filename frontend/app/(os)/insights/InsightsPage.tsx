'use client'

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api-client'
import { motion } from 'framer-motion'
import { GlassCard } from '@/components/os/GlassCard'
import { BarChart3, TrendingUp, Flame, CheckCircle2, Bot, Zap, Clock } from 'lucide-react'
import { BarChart, Bar, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

export function InsightsPage() {
  const queryClient = useQueryClient()

  // Queries
  const { data: workSessions } = useQuery({
    queryKey: ['work_sessions'],
    queryFn: () => api.work.sessions(),
    refetchInterval: 5000,
  })

  const { data: patternsData } = useQuery({
    queryKey: ['productivity-patterns'],
    queryFn: () => api.productivity.patterns(),
    refetchInterval: 5000,
  })

  const { data: suggestionsData } = useQuery({
    queryKey: ['productivity-suggestions'],
    queryFn: () => api.productivity.suggestions(),
    refetchInterval: 5000,
  })

  const { data: insightsData } = useQuery({
    queryKey: ['productivity-insights'],
    queryFn: () => api.productivity.insights(),
    refetchInterval: 5000,
  })

  // Mutations
  const acceptMutation = useMutation({
    mutationFn: (suggestionId: string) => api.productivity.accept(suggestionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['productivity-suggestions'] })
      queryClient.invalidateQueries({ queryKey: ['productivity-patterns'] })
    },
  })

  const dismissMutation = useMutation({
    mutationFn: (suggestionId: string) => api.productivity.dismiss(suggestionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['productivity-suggestions'] })
      queryClient.invalidateQueries({ queryKey: ['productivity-patterns'] })
    },
  })

  // Calculations
  const sessions = workSessions?.sessions || []
  const patterns = patternsData?.patterns || []
  const suggestions = suggestionsData?.suggestions || []

  // Focus time calculation
  const projectDurations: Record<string, number> = {}
  let totalMinutes = 0
  sessions.forEach((s: any) => {
    const proj = s.active_project || 'General'
    const start = new Date(s.started_at).getTime()
    const end = new Date(s.last_updated || s.started_at).getTime()
    const diffMin = Math.max(1, Math.round((end - start) / 60000))
    projectDurations[proj] = (projectDurations[proj] || 0) + diffMin
    totalMinutes += diffMin
  })

  const rawChartData = Object.entries(projectDurations).map(([name, minutes]) => ({
    name,
    hours: parseFloat((minutes / 60).toFixed(1)),
    minutes,
  }))

  const focusChartData = rawChartData
  const activeFocusMinutes = totalMinutes
  const totalFocusHrs = (activeFocusMinutes / 60).toFixed(1)
  const automatedCount = patterns.filter((p: any) => p.automated).length
  const automationRate = patterns.length > 0 ? Math.round((automatedCount / patterns.length) * 100) : 0
  const estimatedSavings = automatedCount > 0 ? (automatedCount * 15) : 0

  const focusScore = typeof insightsData?.focus_score === 'number' && insightsData.focus_score > 0
    ? `${insightsData.focus_score}/100`
    : '0/100'

  const stats = [
    { label: 'Total Focus Time', value: `${totalFocusHrs} hrs`, description: `Score: ${focusScore}`, icon: Clock, color: 'text-[#00d4ff]' },
    { label: 'Patterns Logged', value: patterns.length.toString(), description: 'Repetitive behaviors found', icon: Flame, color: 'text-[#7c3aed]' },
    { label: 'Estimated Savings', value: `${estimatedSavings} min`, description: 'From automated shortcuts', icon: Zap, color: 'text-[#f59e0b]' },
    { label: 'Automation Rate', value: `${automationRate}%`, description: 'Portion of patterns optimized', icon: Bot, color: 'text-[#22c55e]' },
  ]

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="p-6 space-y-6 max-w-[1400px] mx-auto"
    >
      <div>
        <h1 className="text-2xl font-bold text-zinc-100 tracking-tight">Cognitive Insights</h1>
        <p className="text-sm text-zinc-500 mt-1 font-mono">Reducing cognitive effort by tracking focus, repetitive loops, and automation impact.</p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((s) => {
          const Icon = s.icon
          return (
            <GlassCard key={s.label} className="p-4 flex items-center gap-4 border border-[rgba(255,255,255,0.04)]">
              <div className="p-3 rounded-xl bg-[rgba(255,255,255,0.03)] border border-[rgba(255,255,255,0.05)] shrink-0">
                <Icon className={`w-5 h-5 ${s.color}`} />
              </div>
              <div>
                <div className="text-xs text-zinc-500 font-mono">{s.label}</div>
                <div className="text-xl font-bold text-zinc-200 mt-0.5 font-mono">{s.value}</div>
                <div className="text-[10px] text-zinc-600 mt-0.5">{s.description}</div>
              </div>
            </GlassCard>
          )
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Focus Chart (takes 2 cols) */}
        <div className="lg:col-span-2 space-y-6">
          <GlassCard className="p-5 border border-[rgba(255,255,255,0.04)]">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-sm font-semibold text-zinc-300">Focus Distribution by Project</h3>
                <p className="text-[11px] text-zinc-500 font-mono">Aggregated from active project sessions</p>
              </div>
              <BarChart3 className="w-4 h-4 text-zinc-500" />
            </div>
            <div className="h-64 mt-4">
              {focusChartData.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={focusChartData}>
                    <XAxis dataKey="name" stroke="#52525b" tick={{ fontSize: 10, fill: '#71717a' }} />
                    <YAxis stroke="#52525b" tick={{ fontSize: 10, fill: '#71717a' }} />
                    <Tooltip contentStyle={{ background: '#18181b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, fontSize: 11, color: '#e4e4e7' }} />
                    <Bar dataKey="hours" fill="#00d4ff" radius={[6, 6, 0, 0]} maxBarSize={48} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex items-center justify-center h-full text-zinc-700 text-xs font-mono">
                  No focus data available. Start working on a project to capture sessions.
                </div>
              )}
            </div>
          </GlassCard>

          {/* Repeated Behaviors Table */}
          <GlassCard className="p-5 border border-[rgba(255,255,255,0.04)]">
            <h3 className="text-sm font-semibold text-zinc-300 mb-4">Repeated Behaviors (Productivity Patterns)</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs text-zinc-400">
                <thead>
                  <tr className="border-b border-[rgba(255,255,255,0.06)] text-zinc-500 font-mono">
                    <th className="pb-2 font-normal">Action</th>
                    <th className="pb-2 font-normal">Category</th>
                    <th className="pb-2 font-normal">Frequency</th>
                    <th className="pb-2 font-normal">Last Observed</th>
                    <th className="pb-2 font-normal">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[rgba(255,255,255,0.03)]">
                  {patterns.length > 0 ? (
                    patterns.map((p: any) => (
                      <tr key={p.action} className="hover:bg-[rgba(255,255,255,0.01)] transition-colors">
                        <td className="py-2.5 font-mono text-zinc-300 font-semibold">{p.action}</td>
                        <td className="py-2.5 capitalize">{p.category}</td>
                        <td className="py-2.5 text-zinc-300 font-mono font-bold">{p.frequency}x</td>
                        <td className="py-2.5 text-[10px] text-zinc-600 font-mono">
                          {new Date(p.last_observed).toLocaleTimeString()}
                        </td>
                        <td className="py-2.5">
                          {p.automated ? (
                            <span className="inline-flex items-center gap-1 text-[10px] text-[#22c55e] font-medium bg-[rgba(34,197,94,0.08)] px-2 py-0.5 rounded-full border border-[rgba(34,197,94,0.2)]">
                              Automated
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-[10px] text-zinc-500 font-medium bg-[rgba(255,255,255,0.03)] px-2 py-0.5 rounded-full border border-[rgba(255,255,255,0.05)]">
                              Manual
                            </span>
                          )}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={5} className="py-6 text-center text-zinc-700 font-mono">
                        No workflow patterns detected yet. Repetitive actions will appear here.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </GlassCard>
        </div>

        {/* Optimization Opportunities (Suggestions Sidebar) */}
        <div>
          <GlassCard className="p-5 h-full flex flex-col border border-[rgba(255,255,255,0.04)]">
            <div className="flex items-center gap-2 mb-4 shrink-0">
              <TrendingUp className="w-4 h-4 text-[#f59e0b]" />
              <h3 className="text-sm font-semibold text-zinc-300">Optimization Opportunities</h3>
            </div>
            
            <div className="flex-1 overflow-y-auto space-y-4 pr-1">
              {suggestions.length > 0 ? (
                suggestions.map((s: any) => {
                  const isPending = s.status === 'pending'
                  return (
                    <motion.div
                      key={s.suggestion_id}
                      initial={{ opacity: 0, scale: 0.95 }}
                      animate={{ opacity: 1, scale: 1 }}
                      className={`p-4 rounded-xl border transition-all ${
                        isPending
                          ? 'bg-[rgba(245,158,11,0.03)] border-[rgba(245,158,11,0.15)] focus-within:border-[rgba(245,158,11,0.3)]'
                          : s.status === 'accepted'
                          ? 'bg-[rgba(34,197,94,0.02)] border-[rgba(34,197,94,0.1)] opacity-70'
                          : 'bg-[rgba(255,255,255,0.01)] border-[rgba(255,255,255,0.03)] opacity-40'
                      }`}
                    >
                      <div className="flex justify-between items-start gap-2">
                        <h4 className="text-xs font-semibold text-zinc-200">{s.title}</h4>
                        <span className={`text-[9px] uppercase font-mono px-1.5 py-0.5 rounded ${
                          isPending
                            ? 'bg-[rgba(245,158,11,0.1)] text-[#f59e0b]'
                            : s.status === 'accepted'
                            ? 'bg-[rgba(34,197,94,0.1)] text-[#22c55e]'
                            : 'bg-zinc-800 text-zinc-500'
                        }`}>
                          {s.status}
                        </span>
                      </div>
                      <p className="text-[11px] text-zinc-500 mt-1.5 leading-normal">{s.description}</p>
                      
                      {isPending && (
                        <div className="flex items-center gap-2 mt-4">
                          <button
                            onClick={() => acceptMutation.mutate(s.suggestion_id)}
                            disabled={acceptMutation.isPending}
                            className="flex-1 py-1.5 px-3 rounded-lg bg-[#00d4ff] text-[#09090b] text-[10px] font-bold hover:bg-[#00d4ff]/90 transition-colors flex items-center justify-center gap-1 cursor-pointer"
                          >
                            <Zap className="w-3 h-3" />
                            Automate Now
                          </button>
                          <button
                            onClick={() => dismissMutation.mutate(s.suggestion_id)}
                            disabled={dismissMutation.isPending}
                            className="py-1.5 px-2.5 rounded-lg border border-[rgba(255,255,255,0.08)] hover:bg-[rgba(255,255,255,0.03)] text-[10px] text-zinc-500 hover:text-zinc-300 transition-all cursor-pointer"
                          >
                            Dismiss
                          </button>
                        </div>
                      )}
                    </motion.div>
                  )
                })
              ) : (
                <div className="flex flex-col items-center justify-center h-48 text-center text-zinc-700 gap-2 font-mono text-xs">
                  <Bot className="w-8 h-8 opacity-20" />
                  No opportunities currently identified. Continue working, and HELIX will suggest shortcuts.
                </div>
              )}
            </div>
          </GlassCard>
        </div>
      </div>
    </motion.div>
  )
}
