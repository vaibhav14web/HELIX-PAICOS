'use client'

import { cn } from '@/lib/utils'
import { useOSStore, type NavSection } from '@/lib/os-store'
import { motion, AnimatePresence } from 'framer-motion'
import {
  LayoutDashboard,
  MessageSquare,
  Mic,
  GitBranch,
  FolderOpen,
  Globe,
  Bot,
  Workflow,
  ScrollText,
  BarChart3,
  Settings,
  ChevronLeft,
  Zap,
  Cpu,
  Target,
} from 'lucide-react'

interface NavItem {
  id: NavSection
  label: string
  icon: React.ComponentType<{ className?: string }>
  group: 'core' | 'intelligence' | 'system'
  badge?: string
}

const navItems: NavItem[] = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard, group: 'core' },
  { id: 'chat', label: 'Chat', icon: MessageSquare, group: 'core', badge: '3' },
  { id: 'voice', label: 'Voice', icon: Mic, group: 'core' },
  { id: 'memory', label: 'Memory', icon: GitBranch, group: 'intelligence' },
  { id: 'agents', label: 'Agents', icon: Bot, group: 'intelligence', badge: '2' },
  { id: 'automation', label: 'Automation', icon: Workflow, group: 'intelligence' },
  { id: 'goals', label: 'Goals', icon: Target, group: 'intelligence' },
  { id: 'system', label: 'System', icon: Cpu, group: 'system' },
  { id: 'files', label: 'Files', icon: FolderOpen, group: 'system' },
  { id: 'browser', label: 'Browser', icon: Globe, group: 'system' },
  { id: 'insights', label: 'Insights', icon: BarChart3, group: 'intelligence' },
  { id: 'logs', label: 'Logs', icon: ScrollText, group: 'system' },
  { id: 'settings', label: 'Settings', icon: Settings, group: 'system' },
]

const groups = [
  { key: 'core', label: 'Core' },
  { key: 'intelligence', label: 'Intelligence' },
  { key: 'system', label: 'System' },
]

export function Sidebar() {
  const { activeSection, setActiveSection, sidebarCollapsed, toggleSidebar } = useOSStore()

  return (
    <motion.aside
      animate={{ width: sidebarCollapsed ? 64 : 220 }}
      transition={{ duration: 0.25, ease: [0.4, 0, 0.2, 1] }}
      className="relative flex flex-col h-full bg-[#0f0f11] border-r border-[rgba(255,255,255,0.06)] shrink-0 overflow-hidden"
    >
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 py-4 border-b border-[rgba(255,255,255,0.06)]">
        <div className="relative shrink-0">
          <div className="w-8 h-8 rounded-lg bg-[rgba(0,212,255,0.12)] border border-[rgba(0,212,255,0.3)] flex items-center justify-center glow-cyan-sm">
            <Zap className="w-4 h-4 text-[#00d4ff]" />
          </div>
        </div>
        <AnimatePresence initial={false}>
          {!sidebarCollapsed && (
            <motion.div
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -8 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              <div className="font-bold text-sm tracking-[0.15em] text-gradient-cyan whitespace-nowrap">HELIX</div>
              <div className="text-[10px] text-muted-foreground font-mono tracking-widest uppercase whitespace-nowrap">AI Agent Platform</div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-3 overflow-y-auto overflow-x-hidden">
        {groups.map((group) => {
          const items = navItems.filter((i) => i.group === group.key)
          return (
            <div key={group.key} className="mb-4">
              <AnimatePresence initial={false}>
                {!sidebarCollapsed && (
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.15 }}
                    className="px-4 mb-1"
                  >
                    <span className="text-[10px] font-mono uppercase tracking-[0.12em] text-zinc-600">
                      {group.label}
                    </span>
                  </motion.div>
                )}
              </AnimatePresence>
              {items.map((item) => {
                const active = activeSection === item.id
                const Icon = item.icon
                return (
                  <button
                    key={item.id}
                    onClick={() => setActiveSection(item.id)}
                    className={cn(
                      'relative w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-all duration-150 group',
                      sidebarCollapsed && 'justify-center px-0',
                      active
                        ? 'text-[#00d4ff]'
                        : 'text-zinc-500 hover:text-zinc-200',
                    )}
                  >
                    {/* Active indicator */}
                    {active && (
                      <motion.div
                        layoutId="nav-active"
                        className="absolute inset-0 bg-[rgba(0,212,255,0.07)] border-r-2 border-[#00d4ff]"
                        transition={{ duration: 0.2 }}
                      />
                    )}
                    <Icon className={cn('w-4 h-4 relative z-10 shrink-0', active && 'drop-shadow-[0_0_6px_rgba(0,212,255,0.8)]')} />
                    <AnimatePresence initial={false}>
                      {!sidebarCollapsed && (
                        <motion.span
                          initial={{ opacity: 0, x: -6 }}
                          animate={{ opacity: 1, x: 0 }}
                          exit={{ opacity: 0, x: -6 }}
                          transition={{ duration: 0.15 }}
                          className="relative z-10 font-medium whitespace-nowrap"
                        >
                          {item.label}
                        </motion.span>
                      )}
                    </AnimatePresence>
                    {!sidebarCollapsed && item.badge && (
                      <motion.span
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="relative z-10 ml-auto text-[10px] font-mono bg-[rgba(0,212,255,0.15)] text-[#00d4ff] px-1.5 py-0.5 rounded-full"
                      >
                        {item.badge}
                      </motion.span>
                    )}
                  </button>
                )
              })}
            </div>
          )
        })}
      </nav>

      {/* Collapse toggle */}
      <div className="p-3 border-t border-[rgba(255,255,255,0.06)]">
        <button
          onClick={toggleSidebar}
          className="w-full flex items-center justify-center p-2 rounded-lg text-zinc-600 hover:text-zinc-300 hover:bg-[rgba(255,255,255,0.04)] transition-colors"
        >
          <motion.div animate={{ rotate: sidebarCollapsed ? 180 : 0 }} transition={{ duration: 0.25 }}>
            <ChevronLeft className="w-4 h-4" />
          </motion.div>
        </button>
      </div>
    </motion.aside>
  )
}
