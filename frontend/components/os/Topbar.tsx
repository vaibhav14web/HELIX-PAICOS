'use client'

import { useEffect, useState } from 'react'
import { useOSStore } from '@/lib/os-store'
import { Search, Bell, Cpu, MemoryStick, Wifi } from 'lucide-react'
import { motion } from 'framer-motion'

function useClock() {
  const [time, setTime] = useState('')
  const [date, setDate] = useState('')
  useEffect(() => {
    const update = () => {
      const now = new Date()
      setTime(now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }))
      setDate(now.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }))
    }
    update()
    const id = setInterval(update, 1000)
    return () => clearInterval(id)
  }, [])
  return { time, date }
}

function useSystemMetrics() {
  const [cpu, setCpu] = useState(34)
  const [mem, setMem] = useState(61)
  useEffect(() => {
    const id = setInterval(() => {
      setCpu((v) => Math.max(5, Math.min(95, v + (Math.random() - 0.5) * 8)))
      setMem((v) => Math.max(40, Math.min(88, v + (Math.random() - 0.5) * 3)))
    }, 2000)
    return () => clearInterval(id)
  }, [])
  return { cpu: Math.round(cpu), mem: Math.round(mem) }
}

const sectionTitles: Record<string, string> = {
  dashboard: 'Dashboard',
  chat: 'AI Chat',
  voice: 'Voice Interface',
  memory: 'Memory Graph',
  files: 'File System',
  browser: 'Browser',
  agents: 'Agent Network',
  automation: 'Automation Studio',
  logs: 'System Logs',
  insights: 'Cognitive Insights',
  settings: 'Settings',
}

export function Topbar() {
  const { activeSection, openCommandPalette } = useOSStore()
  const { time, date } = useClock()
  const { cpu, mem } = useSystemMetrics()
  const [notifications] = useState(3)

  return (
    <header className="h-12 shrink-0 flex items-center justify-between px-4 border-b border-[rgba(255,255,255,0.06)] bg-[#09090b]/80 backdrop-blur-sm">
      {/* Left: breadcrumb */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-zinc-600 font-mono">HELIX</span>
        <span className="text-zinc-700 text-xs">/</span>
        <span className="text-sm font-medium text-zinc-200">{sectionTitles[activeSection] ?? activeSection}</span>
      </div>

      {/* Center: search trigger */}
      <button
        onClick={openCommandPalette}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[rgba(255,255,255,0.04)] border border-[rgba(255,255,255,0.07)] text-zinc-500 hover:text-zinc-300 hover:border-[rgba(0,212,255,0.2)] transition-all text-xs"
      >
        <Search className="w-3 h-3" />
        <span className="hidden md:inline">Search or command...</span>
        <span className="hidden md:inline text-[10px] bg-[rgba(255,255,255,0.06)] px-1.5 py-0.5 rounded font-mono">⌘K</span>
      </button>

      {/* Right: system metrics + clock */}
      <div className="flex items-center gap-4">
        {/* CPU */}
        <div className="hidden md:flex items-center gap-1.5">
          <Cpu className="w-3 h-3 text-zinc-600" />
          <div className="w-14 h-1 bg-[rgba(255,255,255,0.06)] rounded-full overflow-hidden">
            <motion.div
              className="h-full bg-[#00d4ff] rounded-full"
              animate={{ width: `${cpu}%` }}
              transition={{ duration: 1.5, ease: 'easeInOut' }}
            />
          </div>
          <span className="text-[10px] font-mono text-zinc-500">{cpu}%</span>
        </div>

        {/* MEM */}
        <div className="hidden md:flex items-center gap-1.5">
          <MemoryStick className="w-3 h-3 text-zinc-600" />
          <div className="w-14 h-1 bg-[rgba(255,255,255,0.06)] rounded-full overflow-hidden">
            <motion.div
              className="h-full bg-[#7c3aed] rounded-full"
              animate={{ width: `${mem}%` }}
              transition={{ duration: 1.5, ease: 'easeInOut' }}
            />
          </div>
          <span className="text-[10px] font-mono text-zinc-500">{mem}%</span>
        </div>

        {/* Network status */}
        <div className="hidden md:flex items-center gap-1">
          <Wifi className="w-3 h-3 text-[#22c55e]" />
          <span className="text-[10px] font-mono text-zinc-600">LAN</span>
        </div>

        {/* Notifications */}
        <button className="relative p-1.5 rounded-lg hover:bg-[rgba(255,255,255,0.05)] transition-colors">
          <Bell className="w-4 h-4 text-zinc-500" />
          {notifications > 0 && (
            <span className="absolute top-0.5 right-0.5 w-2 h-2 bg-[#00d4ff] rounded-full animate-pulse-glow" />
          )}
        </button>

        {/* Clock */}
        <div className="text-right hidden sm:block">
          <div className="text-xs font-mono text-zinc-200 tabular-nums">{time}</div>
          <div className="text-[10px] font-mono text-zinc-600">{date}</div>
        </div>
      </div>
    </header>
  )
}
