'use client'

import { motion } from 'framer-motion'
import { GlassCard } from '@/components/os/GlassCard'
import { BarChart3, TrendingUp, MessageSquare, Bot, Zap, Clock } from 'lucide-react'
import { AreaChart, Area, BarChart, Bar, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

const usageData = Array.from({ length: 24 }, (_, i) => ({
  hour: `${i}:00`,
  messages: Math.floor(Math.random() * 80 + 10),
  agents: Math.floor(Math.random() * 5),
  tokens: Math.floor(Math.random() * 50000 + 5000),
}))

const dailyStats = [
  { label: 'Messages Sent', value: '2,841', trend: '+12%', icon: MessageSquare, color: 'text-[#00d4ff]' },
  { label: 'Agent Tasks', value: '847', trend: '+8%', icon: Bot, color: 'text-[#7c3aed]' },
  { label: 'Tokens Used', value: '4.8M', trend: '+21%', icon: Zap, color: 'text-[#f59e0b]' },
  { label: 'Avg Response', value: '1.2s', trend: '-5%', icon: Clock, color: 'text-[#22c55e]' },
]

export function AnalyticsPage() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="p-6 space-y-6 max-w-[1400px] mx-auto"
    >
      <div>
        <h1 className="text-2xl font-bold text-zinc-100 tracking-tight">Analytics</h1>
        <p className="text-sm text-zinc-500 mt-1">Usage metrics and system performance</p>
      </div>

      <div className="grid grid-cols-4 gap-3">
        {dailyStats.map((s) => {
          const Icon = s.icon
          return (
            <GlassCard key={s.label} className="flex items-center gap-3">
              <div className={cn('p-2 rounded-lg bg-[rgba(0,212,255,0.08)] shrink-0', s.color)}>
                <Icon className="w-4 h-4" />
              </div>
              <div>
                <div className={cn('text-lg font-bold font-mono', s.color)}>{s.value}</div>
                <div className="text-[10px] text-zinc-600">{s.label}</div>
                <div className="text-[10px] text-[#22c55e] font-mono">{s.trend}</div>
              </div>
            </GlassCard>
          )
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <GlassCard>
          <h3 className="text-xs font-mono uppercase tracking-widest text-zinc-600 mb-4">Messages per Hour</h3>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={usageData}>
              <defs>
                <linearGradient id="msgGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#00d4ff" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#00d4ff" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="hour" tick={{ fontSize: 10, fill: '#52525b' }} />
              <YAxis tick={{ fontSize: 10, fill: '#52525b' }} />
              <Tooltip contentStyle={{ background: '#18181b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, fontSize: 12 }} />
              <Area type="monotone" dataKey="messages" stroke="#00d4ff" strokeWidth={2} fill="url(#msgGrad)" />
            </AreaChart>
          </ResponsiveContainer>
        </GlassCard>

        <GlassCard>
          <h3 className="text-xs font-mono uppercase tracking-widest text-zinc-600 mb-4">Agent Activity</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={usageData}>
              <XAxis dataKey="hour" tick={{ fontSize: 10, fill: '#52525b' }} />
              <YAxis tick={{ fontSize: 10, fill: '#52525b' }} />
              <Tooltip contentStyle={{ background: '#18181b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, fontSize: 12 }} />
              <Bar dataKey="agents" fill="#7c3aed" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </GlassCard>
      </div>
    </motion.div>
  )
}

function cn(...classes: (string | false | undefined | null)[]) {
  return classes.filter(Boolean).join(' ')
}
