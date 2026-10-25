'use client'

import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useOSStore, type NavSection } from '@/lib/os-store'
import {
  Search, LayoutDashboard, MessageSquare, Mic, GitBranch,
  FolderOpen, Globe, Bot, Workflow, ScrollText, BarChart3,
  Settings, Zap, Terminal, RefreshCw,
} from 'lucide-react'
import { cn } from '@/lib/utils'

interface Command {
  id: string
  label: string
  description?: string
  icon: React.ComponentType<{ className?: string }>
  action: () => void
  category: string
}

export function CommandPalette() {
  const { commandPaletteOpen, closeCommandPalette, setActiveSection } = useOSStore()
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const nav = (id: NavSection) => () => { setActiveSection(id); closeCommandPalette() }

  const allCommands: Command[] = [
    { id: 'dashboard', label: 'Open Dashboard', icon: LayoutDashboard, action: nav('dashboard'), category: 'Navigate' },
    { id: 'chat', label: 'Open AI Chat', icon: MessageSquare, action: nav('chat'), category: 'Navigate' },
    { id: 'voice', label: 'Open Voice Interface', icon: Mic, action: nav('voice'), category: 'Navigate' },
    { id: 'memory', label: 'Open Memory Graph', icon: GitBranch, action: nav('memory'), category: 'Navigate' },
    { id: 'agents', label: 'Open Agent Network', icon: Bot, action: nav('agents'), category: 'Navigate' },
    { id: 'automation', label: 'Open Automation Studio', icon: Workflow, action: nav('automation'), category: 'Navigate' },
    { id: 'files', label: 'Open File System', icon: FolderOpen, action: nav('files'), category: 'Navigate' },
    { id: 'browser', label: 'Open Browser', icon: Globe, action: nav('browser'), category: 'Navigate' },
    { id: 'insights', label: 'Open Cognitive Insights', icon: BarChart3, action: nav('insights'), category: 'Navigate' },
    { id: 'logs', label: 'Open System Logs', icon: ScrollText, action: nav('logs'), category: 'Navigate' },
    { id: 'settings', label: 'Open Settings', icon: Settings, action: nav('settings'), category: 'Navigate' },
    { id: 'new-agent', label: 'Deploy New Agent', description: 'Spawn a new AI agent task', icon: Zap, action: nav('agents'), category: 'Actions' },
    { id: 'run-automation', label: 'Run Automation Flow', description: 'Execute an automation pipeline', icon: RefreshCw, action: nav('automation'), category: 'Actions' },
    { id: 'terminal', label: 'Open Terminal', description: 'Launch shell session', icon: Terminal, action: nav('logs'), category: 'Actions' },
  ]

  const filtered = query.trim()
    ? allCommands.filter(
        (c) =>
          c.label.toLowerCase().includes(query.toLowerCase()) ||
          c.description?.toLowerCase().includes(query.toLowerCase()) ||
          c.category.toLowerCase().includes(query.toLowerCase()),
      )
    : allCommands

  const categories = Array.from(new Set(filtered.map((c) => c.category)))

  useEffect(() => { setSelected(0) }, [query])

  useEffect(() => {
    if (commandPaletteOpen) {
      setTimeout(() => inputRef.current?.focus(), 50)
      setQuery('')
      setSelected(0)
    }
  }, [commandPaletteOpen])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        commandPaletteOpen ? closeCommandPalette() : useOSStore.getState().openCommandPalette()
      }
      if (!commandPaletteOpen) return
      if (e.key === 'Escape') closeCommandPalette()
      if (e.key === 'ArrowDown') { e.preventDefault(); setSelected((s) => Math.min(s + 1, filtered.length - 1)) }
      if (e.key === 'ArrowUp') { e.preventDefault(); setSelected((s) => Math.max(s - 1, 0)) }
      if (e.key === 'Enter') { filtered[selected]?.action(); closeCommandPalette() }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [commandPaletteOpen, filtered, selected, closeCommandPalette])

  let flatIdx = 0

  return (
    <AnimatePresence>
      {commandPaletteOpen && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
            onClick={closeCommandPalette}
          />
          {/* Panel */}
          <motion.div
            initial={{ opacity: 0, scale: 0.96, y: -10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: -10 }}
            transition={{ duration: 0.18, ease: [0.4, 0, 0.2, 1] }}
            className="fixed top-[18%] left-1/2 -translate-x-1/2 z-50 w-full max-w-xl glass rounded-2xl overflow-hidden border border-[rgba(0,212,255,0.15)] glow-cyan shadow-2xl"
          >
            {/* Input */}
            <div className="flex items-center gap-3 px-4 py-3 border-b border-[rgba(255,255,255,0.06)]">
              <Search className="w-4 h-4 text-zinc-500 shrink-0" />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search commands, pages, agents..."
                className="flex-1 bg-transparent text-sm text-zinc-100 placeholder:text-zinc-600 outline-none"
              />
              <kbd className="text-[10px] font-mono bg-[rgba(255,255,255,0.05)] text-zinc-600 px-1.5 py-0.5 rounded border border-[rgba(255,255,255,0.08)]">
                ESC
              </kbd>
            </div>

            {/* Results */}
            <div className="max-h-80 overflow-y-auto py-2">
              {filtered.length === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-zinc-600">No commands found</div>
              ) : (
                categories.map((cat) => (
                  <div key={cat}>
                    <div className="px-4 py-1.5">
                      <span className="text-[10px] font-mono uppercase tracking-widest text-zinc-700">{cat}</span>
                    </div>
                    {filtered
                      .filter((c) => c.category === cat)
                      .map((cmd) => {
                        const idx = flatIdx++
                        const isSelected = idx === selected
                        const Icon = cmd.icon
                        return (
                          <button
                            key={cmd.id}
                            onClick={() => { cmd.action(); closeCommandPalette() }}
                            onMouseEnter={() => setSelected(idx)}
                            className={cn(
                              'w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors',
                              isSelected ? 'bg-[rgba(0,212,255,0.08)]' : 'hover:bg-[rgba(255,255,255,0.03)]',
                            )}
                          >
                            <div className={cn(
                              'w-7 h-7 rounded-lg flex items-center justify-center shrink-0',
                              isSelected ? 'bg-[rgba(0,212,255,0.15)]' : 'bg-[rgba(255,255,255,0.05)]',
                            )}>
                              <Icon className={cn('w-3.5 h-3.5', isSelected ? 'text-[#00d4ff]' : 'text-zinc-500')} />
                            </div>
                            <div>
                              <div className={cn('text-sm', isSelected ? 'text-zinc-100' : 'text-zinc-400')}>{cmd.label}</div>
                              {cmd.description && <div className="text-xs text-zinc-600">{cmd.description}</div>}
                            </div>
                          </button>
                        )
                      })}
                  </div>
                ))
              )}
            </div>

            {/* Footer */}
            <div className="px-4 py-2 border-t border-[rgba(255,255,255,0.06)] flex items-center gap-4">
              {[['↑↓', 'Navigate'], ['↵', 'Select'], ['ESC', 'Close']].map(([key, label]) => (
                <div key={key} className="flex items-center gap-1.5">
                  <kbd className="text-[10px] font-mono bg-[rgba(255,255,255,0.05)] text-zinc-500 px-1.5 py-0.5 rounded">{key}</kbd>
                  <span className="text-[10px] text-zinc-700">{label}</span>
                </div>
              ))}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
