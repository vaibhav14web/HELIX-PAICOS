'use client'

import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  ChevronLeft,
  ChevronRight,
  RotateCcw,
  Lock,
  Plus,
  X,
  Globe,
  Star,
  Zap,
  Search,
  ExternalLink,
  Sparkles,
  Bookmark,
  BookmarkCheck,
  Brain,
  Check,
  AlertCircle,
  HelpCircle,
  FileText,
  Copy,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { GlassCard } from '@/components/os/GlassCard'
import { api, BrowserBookmarkItem, BrowserQuickLinkItem, BrowserPageSummary } from '@/lib/api-client'
import { useAggregatedContext } from '@/lib/hooks/use-context'

export interface BrowserTab {
  id: string
  title: string
  url: string
  inputUrl: string
  favicon: string
  active: boolean
  isLoading: boolean
  history: string[]
  historyIndex: number
  key: number
}

const DEFAULT_BOOKMARKS: BrowserBookmarkItem[] = [
  { id: 'bm-1', label: 'GitHub', icon: '💻', url: 'https://github.com' },
  { id: 'bm-2', label: 'Docs', icon: '📄', url: 'https://opencode.ai/docs' },
  { id: 'bm-3', label: 'AI Hub', icon: '🤖', url: 'https://huggingface.co' },
  { id: 'bm-4', label: 'Analytics', icon: '📊', url: 'https://analytics.google.com' },
]

const DEFAULT_QUICK_LINKS: BrowserQuickLinkItem[] = [
  { id: 'ql-1', label: 'HELIX Home', icon: '🏠', url: 'https://helix.ai', color: 'text-cyan-400', desc: 'AI platform dashboard' },
  { id: 'ql-2', label: 'Research', icon: '🔬', url: 'https://scholar.google.com', color: 'text-indigo-400', desc: 'Academic search' },
  { id: 'ql-3', label: 'Models', icon: '🧠', url: 'https://huggingface.co/models', color: 'text-amber-400', desc: 'Open model repository' },
  { id: 'ql-4', label: 'Code', icon: '📝', url: 'https://github.com', color: 'text-emerald-400', desc: 'Source repositories' },
  { id: 'ql-5', label: 'News', icon: '📰', url: 'https://news.ycombinator.com', color: 'text-orange-400', desc: 'Hacker News tech updates' },
  { id: 'ql-6', label: 'MCP', icon: '🔌', url: 'https://opencode.ai/mcp', color: 'text-purple-400', desc: 'Plugin & tool registry' },
  { id: 'ql-7', label: 'DuckDuckGo', icon: '🦆', url: 'https://duckduckgo.com', color: 'text-yellow-400', desc: 'Privacy search engine' },
  { id: 'ql-8', label: 'Chat', icon: '💬', url: 'https://chat.helix.ai', color: 'text-sky-400', desc: 'AI assistant context' },
]

const AI_SUGGESTIONS = [
  'Summarize the top AI papers from today',
  'Find recent research on autonomous multi-agent systems',
  'Compare GPT-5 vs Claude 4 benchmarks',
  'What is trending on Hacker News?',
]

function normalizeBrowserInput(raw: string): string {
  const trimmed = raw.trim()
  if (!trimmed) return ''

  // Freeform search query (contains spaces or no dot and no scheme)
  const hasSpaces = trimmed.includes(' ')
  const hasDots = trimmed.includes('.')
  const hasScheme = trimmed.includes('://')

  if ((hasSpaces || !hasDots) && !hasScheme) {
    return `https://html.duckduckgo.com/html/?q=${encodeURIComponent(trimmed)}`
  }

  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed
  }

  return `https://${trimmed}`
}

function getHostnameFromUrl(urlStr: string): string {
  try {
    const parsed = new URL(urlStr)
    return parsed.hostname.replace(/^www\./, '') || 'Page'
  } catch {
    return 'Webpage'
  }
}

export function BrowserPage() {
  // Desktop windows context (observed desktop applications)
  const { data: contextData } = useAggregatedContext()
  const browserObj = contextData?.context?.browser as { tabs?: Array<{ title: string; browser?: string }> } | undefined
  const desktopObservedTabs = browserObj?.tabs || []

  // Internal Browser Tabs
  const [tabs, setTabs] = useState<BrowserTab[]>(() => {
    return [
      {
        id: 'tab-initial-1',
        title: 'New Tab',
        url: '',
        inputUrl: '',
        favicon: '🌐',
        active: true,
        isLoading: false,
        history: [],
        historyIndex: -1,
        key: Date.now(),
      },
    ]
  })

  // Bookmarks & Quicklinks State
  const [bookmarks, setBookmarks] = useState<BrowserBookmarkItem[]>(DEFAULT_BOOKMARKS)
  const [quickLinks, setQuickLinks] = useState<BrowserQuickLinkItem[]>(DEFAULT_QUICK_LINKS)

  // AI Assistant State
  const [aiDrawerOpen, setAiDrawerOpen] = useState(false)
  const [aiQuestion, setAiQuestion] = useState('')
  const [aiSummaryData, setAiSummaryData] = useState<BrowserPageSummary | null>(null)
  const [isAiLoading, setIsAiLoading] = useState(false)
  const [aiError, setAiError] = useState('')
  const [isMemorySaved, setIsMemorySaved] = useState(false)
  const [copiedSummary, setCopiedSummary] = useState(false)

  // Active Tab derived
  const activeTab = useMemo(() => {
    return tabs.find((t) => t.active) || tabs[0]
  }, [tabs])

  const canGoBack = activeTab.historyIndex > 0
  const canGoForward = activeTab.historyIndex >= 0 && activeTab.historyIndex < activeTab.history.length - 1

  // Load Bookmarks and Quick Links from API / LocalStorage
  useEffect(() => {
    let mounted = true
    const loadData = async () => {
      try {
        const bmRes = await api.browser.getBookmarks()
        if (mounted && bmRes?.bookmarks && bmRes.bookmarks.length > 0) {
          setBookmarks(bmRes.bookmarks)
        }
      } catch {
        const cached = localStorage.getItem('helix_browser_bookmarks')
        if (cached && mounted) {
          try {
            setBookmarks(JSON.parse(cached))
          } catch {}
        }
      }

      try {
        const qlRes = await api.browser.getQuickLinks()
        if (mounted && qlRes?.quick_links && qlRes.quick_links.length > 0) {
          setQuickLinks(qlRes.quick_links)
        }
      } catch {
        const cached = localStorage.getItem('helix_browser_quicklinks')
        if (cached && mounted) {
          try {
            setQuickLinks(JSON.parse(cached))
          } catch {}
        }
      }
    }
    loadData()
    return () => {
      mounted = false
    }
  }, [])

  // Check if active URL is bookmarked
  const isBookmarked = useMemo(() => {
    if (!activeTab.url) return false
    return bookmarks.some((b) => b.url.toLowerCase() === activeTab.url.toLowerCase())
  }, [bookmarks, activeTab.url])

  // Navigation: Navigate active tab to a URL
  const navigateActiveTab = useCallback((target: string) => {
    const nextUrl = normalizeBrowserInput(target)
    if (!nextUrl) return

    setTabs((prev) =>
      prev.map((tab) => {
        if (!tab.active) return tab

        const truncatedHistory = tab.history.slice(0, tab.historyIndex + 1)
        const updatedHistory = [...truncatedHistory, nextUrl]
        const updatedIndex = updatedHistory.length - 1

        return {
          ...tab,
          url: nextUrl,
          inputUrl: nextUrl,
          title: getHostnameFromUrl(nextUrl),
          isLoading: true,
          history: updatedHistory,
          historyIndex: updatedIndex,
          key: Date.now(),
        }
      })
    )
    setAiSummaryData(null)
    setAiError('')
  }, [])

  // Navigation: Back
  const handleBack = useCallback(() => {
    setTabs((prev) =>
      prev.map((tab) => {
        if (!tab.active || tab.historyIndex <= 0) return tab
        const nextIndex = tab.historyIndex - 1
        const nextUrl = tab.history[nextIndex]
        return {
          ...tab,
          url: nextUrl,
          inputUrl: nextUrl,
          title: getHostnameFromUrl(nextUrl),
          historyIndex: nextIndex,
          isLoading: true,
          key: Date.now(),
        }
      })
    )
    setAiSummaryData(null)
  }, [])

  // Navigation: Forward
  const handleForward = useCallback(() => {
    setTabs((prev) =>
      prev.map((tab) => {
        if (!tab.active || tab.historyIndex >= tab.history.length - 1) return tab
        const nextIndex = tab.historyIndex + 1
        const nextUrl = tab.history[nextIndex]
        return {
          ...tab,
          url: nextUrl,
          inputUrl: nextUrl,
          title: getHostnameFromUrl(nextUrl),
          historyIndex: nextIndex,
          isLoading: true,
          key: Date.now(),
        }
      })
    )
    setAiSummaryData(null)
  }, [])

  // Navigation: Reload
  const handleReload = useCallback(() => {
    setTabs((prev) =>
      prev.map((tab) => {
        if (!tab.active || !tab.url) return tab
        return {
          ...tab,
          isLoading: true,
          key: Date.now(),
        }
      })
    )
  }, [])

  // Tabs: Add Tab
  const handleAddTab = useCallback((initialUrl: string = '') => {
    const id = `tab-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    const normalized = initialUrl ? normalizeBrowserInput(initialUrl) : ''
    const newTab: BrowserTab = {
      id,
      title: normalized ? getHostnameFromUrl(normalized) : 'New Tab',
      url: normalized,
      inputUrl: normalized,
      favicon: '🌐',
      active: true,
      isLoading: Boolean(normalized),
      history: normalized ? [normalized] : [],
      historyIndex: normalized ? 0 : -1,
      key: Date.now(),
    }
    setTabs((prev) => prev.map((t) => ({ ...t, active: false })).concat(newTab))
  }, [])

  // Tabs: Close Tab
  const handleCloseTab = useCallback((id: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation()
    setTabs((prev) => {
      const idx = prev.findIndex((t) => t.id === id)
      if (idx === -1) return prev

      const remaining = prev.filter((t) => t.id !== id)
      if (remaining.length === 0) {
        return [
          {
            id: `tab-${Date.now()}`,
            title: 'New Tab',
            url: '',
            inputUrl: '',
            favicon: '🌐',
            active: true,
            isLoading: false,
            history: [],
            historyIndex: -1,
            key: Date.now(),
          },
        ]
      }

      if (prev[idx].active) {
        const nextActiveIdx = Math.min(idx, remaining.length - 1)
        remaining[nextActiveIdx].active = true
      }
      return remaining
    })
  }, [])

  // Tabs: Switch Tab
  const handleSwitchTab = useCallback((id: string) => {
    setTabs((prev) => prev.map((t) => ({ ...t, active: t.id === id })))
    setAiSummaryData(null)
    setAiError('')
  }, [])

  // URL Input Change
  const handleInputChange = (val: string) => {
    setTabs((prev) =>
      prev.map((t) => (t.active ? { ...t, inputUrl: val } : t))
    )
  }

  // Toggle Bookmark
  const handleToggleBookmark = async () => {
    if (!activeTab.url) return
    const currentUrl = activeTab.url
    const currentTitle = activeTab.title || getHostnameFromUrl(currentUrl)

    if (isBookmarked) {
      const existing = bookmarks.find((b) => b.url.toLowerCase() === currentUrl.toLowerCase())
      if (existing) {
        const updated = bookmarks.filter((b) => b.id !== existing.id)
        setBookmarks(updated)
        localStorage.setItem('helix_browser_bookmarks', JSON.stringify(updated))
        try {
          await api.browser.deleteBookmark(existing.id)
        } catch {}
      }
    } else {
      const newBm: BrowserBookmarkItem = {
        id: `bm-${Date.now()}`,
        label: currentTitle,
        url: currentUrl,
        icon: '⭐',
      }
      const updated = [...bookmarks, newBm]
      setBookmarks(updated)
      localStorage.setItem('helix_browser_bookmarks', JSON.stringify(updated))
      try {
        await api.browser.addBookmark(newBm)
      } catch {}
    }
  }

  // Open in Desktop Native Browser
  const handleOpenNative = async (urlToOpen: string) => {
    try {
      await api.browser.open(urlToOpen)
    } catch {
      window.open(urlToOpen, '_blank', 'noreferrer')
    }
  }

  // AI Summarization
  const handleGenerateSummary = async (overrideQuestion?: string) => {
    if (!activeTab.url) return
    setIsAiLoading(true)
    setAiError('')
    setIsMemorySaved(false)
    try {
      const q = overrideQuestion !== undefined ? overrideQuestion : aiQuestion
      const res = await api.browser.summarize(activeTab.url, q || undefined, false)
      setAiSummaryData(res)
    } catch (err: any) {
      setAiError(err?.message || 'Could not summarize this webpage.')
    } finally {
      setIsAiLoading(false)
    }
  }

  // Save Summary to HELIX Memory
  const handleSaveToMemory = async () => {
    if (!aiSummaryData?.summary) return
    try {
      await api.warmMemory.index({
        text: `Browser research for '${aiSummaryData.title || activeTab.url}':\n${aiSummaryData.summary}`,
        category: 'browser_research',
        metadata: {
          url: activeTab.url,
          title: aiSummaryData.title || activeTab.title,
          timestamp: Date.now(),
        },
      })
      setIsMemorySaved(true)
    } catch (err: any) {
      setAiError('Failed to save to warm memory.')
    }
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    setCopiedSummary(true)
    setTimeout(() => setCopiedSummary(false), 2000)
  }

  const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
  const iframeSrc = activeTab.url ? `${API_BASE}/browser/proxy?url=${encodeURIComponent(activeTab.url)}` : ''

  return (
    <div className="flex flex-col h-full bg-[#0a0a0c] text-zinc-200 select-none overflow-hidden">
      {/* ── Top Tabs Bar ── */}
      <div className="flex items-center gap-1 px-2 pt-2 bg-[#0e0e12] border-b border-white/[0.06] shrink-0 overflow-x-auto scrollbar-none">
        {tabs.map((tab) => (
          <div
            key={tab.id}
            role="button"
            tabIndex={0}
            onClick={() => handleSwitchTab(tab.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                handleSwitchTab(tab.id)
              }
            }}
            className={cn(
              'group relative flex items-center gap-2 px-3 py-1.5 rounded-t-lg min-w-[120px] max-w-[200px] text-xs transition-all cursor-pointer border-t border-x',
              tab.active
                ? 'bg-[#141419] text-zinc-100 border-white/[0.1] shadow-lg'
                : 'bg-white/[0.02] text-zinc-500 border-transparent hover:bg-white/[0.04] hover:text-zinc-300'
            )}
          >
            {tab.active && (
              <span className="absolute top-0 left-2 right-2 h-[2px] bg-gradient-to-r from-cyan-400 to-blue-500 rounded-full" />
            )}
            <span className="text-xs shrink-0">{tab.favicon}</span>
            <span className="truncate flex-1 font-medium">{tab.title || 'New Tab'}</span>
            <button
              type="button"
              onClick={(e) => handleCloseTab(tab.id, e)}
              className="opacity-0 group-hover:opacity-100 p-0.5 rounded-md hover:bg-white/[0.1] text-zinc-400 hover:text-zinc-100 transition-all shrink-0"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        ))}

        <button
          onClick={() => handleAddTab('')}
          className="p-1.5 ml-1 rounded-lg text-zinc-500 hover:text-cyan-400 hover:bg-white/[0.04] transition-colors shrink-0"
          title="New Tab"
        >
          <Plus className="w-4 h-4" />
        </button>
      </div>

      {/* ── Navigation & Address Bar ── */}
      <div className="flex items-center gap-2 px-3 py-2 bg-[#121217] border-b border-white/[0.06] shrink-0">
        <div className="flex items-center gap-1">
          <button
            onClick={handleBack}
            disabled={!canGoBack}
            className={cn(
              'p-1.5 rounded-lg transition-colors',
              canGoBack
                ? 'text-zinc-300 hover:text-white hover:bg-white/[0.06]'
                : 'text-zinc-600 cursor-not-allowed'
            )}
            title="Back"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <button
            onClick={handleForward}
            disabled={!canGoForward}
            className={cn(
              'p-1.5 rounded-lg transition-colors',
              canGoForward
                ? 'text-zinc-300 hover:text-white hover:bg-white/[0.06]'
                : 'text-zinc-600 cursor-not-allowed'
            )}
            title="Forward"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
          <button
            onClick={handleReload}
            className="p-1.5 rounded-lg text-zinc-300 hover:text-cyan-400 hover:bg-white/[0.06] transition-colors"
            title="Reload"
          >
            <RotateCcw className={cn('w-4 h-4', activeTab.isLoading && 'animate-spin text-cyan-400')} />
          </button>
        </div>

        {/* Address Input */}
        <div className="flex-1 flex items-center gap-2 px-3 py-1.5 rounded-xl bg-black/40 border border-white/[0.08] focus-within:border-cyan-500/50 focus-within:ring-1 focus-within:ring-cyan-500/30 transition-all">
          <Lock className={cn('w-3.5 h-3.5 shrink-0', activeTab.url.startsWith('https://') ? 'text-emerald-400' : 'text-zinc-500')} />
          <input
            type="text"
            value={activeTab.inputUrl}
            placeholder="Search DuckDuckGo or enter web address..."
            onChange={(e) => handleInputChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                navigateActiveTab(activeTab.inputUrl)
              }
            }}
            className="flex-1 bg-transparent text-xs text-zinc-200 placeholder-zinc-600 outline-none font-sans"
          />
          {activeTab.inputUrl && (
            <button
              onClick={() => handleInputChange('')}
              className="text-zinc-500 hover:text-zinc-300 p-0.5"
            >
              <X className="w-3 h-3" />
            </button>
          )}
          <button
            onClick={() => navigateActiveTab(activeTab.inputUrl)}
            className="rounded-md bg-cyan-500/15 hover:bg-cyan-500/25 border border-cyan-500/30 px-2 py-0.5 text-[11px] font-medium text-cyan-400 transition-colors"
          >
            Go
          </button>
        </div>

        {/* Bookmarks Bar Shortcuts */}
        <div className="hidden lg:flex items-center gap-1">
          {bookmarks.slice(0, 4).map((b) => (
            <button
              key={b.id}
              onClick={() => navigateActiveTab(b.url)}
              className="flex items-center gap-1 px-2 py-1 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-white/[0.04] transition-colors text-xs"
              title={b.url}
            >
              <span>{b.icon}</span>
              <span className="hidden xl:inline text-[11px] font-medium">{b.label}</span>
            </button>
          ))}
        </div>

        {/* Star / Bookmark Toggle Button */}
        <button
          onClick={handleToggleBookmark}
          disabled={!activeTab.url}
          className={cn(
            'p-1.5 rounded-lg transition-colors',
            isBookmarked
              ? 'text-amber-400 hover:text-amber-300 hover:bg-amber-400/10'
              : 'text-zinc-500 hover:text-amber-400 hover:bg-white/[0.04]',
            !activeTab.url && 'opacity-40 cursor-not-allowed'
          )}
          title={isBookmarked ? 'Remove Bookmark' : 'Bookmark Page'}
        >
          <Star className={cn('w-4 h-4', isBookmarked && 'fill-amber-400')} />
        </button>

        {/* AI Assistant Drawer Toggle */}
        <button
          onClick={() => setAiDrawerOpen((prev) => !prev)}
          className={cn(
            'flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all border',
            aiDrawerOpen
              ? 'bg-cyan-500/20 border-cyan-400/40 text-cyan-300 shadow-[0_0_12px_rgba(0,212,255,0.25)]'
              : 'bg-white/[0.04] border-white/[0.08] text-zinc-300 hover:border-cyan-500/30 hover:text-cyan-400'
          )}
          title="Toggle AI Page Assistant"
        >
          <Sparkles className="w-3.5 h-3.5 text-cyan-400" />
          <span className="hidden sm:inline">AI Assistant</span>
        </button>
      </div>

      {/* ── Real Loading Progress Bar ── */}
      {activeTab.isLoading && (
        <div className="h-[2px] w-full bg-cyan-950/40 overflow-hidden shrink-0">
          <motion.div
            className="h-full bg-gradient-to-r from-cyan-500 via-sky-400 to-blue-500 shadow-[0_0_8px_rgba(0,212,255,0.8)]"
            initial={{ x: '-100%' }}
            animate={{ x: '100%' }}
            transition={{ repeat: Infinity, duration: 1.2, ease: 'linear' }}
          />
        </div>
      )}

      {/* ── Main Viewport Area ── */}
      <div className="flex-1 flex overflow-hidden relative">
        <div className="flex-1 flex flex-col h-full overflow-hidden bg-[#0a0a0c]">
          {!activeTab.url ? (
            /* ── New Tab Dashboard ── */
            <div className="flex-1 overflow-y-auto p-6 lg:p-10 max-w-4xl mx-auto w-full space-y-8">
              {/* Header */}
              <div className="text-center space-y-3 pt-6">
                <div className="inline-flex p-3 rounded-2xl bg-cyan-500/10 border border-cyan-500/20 shadow-[0_0_20px_rgba(0,212,255,0.15)]">
                  <Globe className="w-8 h-8 text-cyan-400" />
                </div>
                <h1 className="text-2xl font-bold tracking-tight text-zinc-100">HELIX Browser</h1>
                <p className="text-sm text-zinc-400 max-w-md mx-auto">
                  Isolated, secure web subsystem with AI research assistant and verified memory synthesis.
                </p>
              </div>

              {/* Quick Links Grid */}
              <div>
                <h2 className="text-xs font-mono uppercase tracking-widest text-zinc-500 mb-3 flex items-center gap-1.5">
                  <Zap className="w-3.5 h-3.5 text-cyan-400" /> Quick Links
                </h2>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  {quickLinks.map((link) => (
                    <button
                      key={link.id}
                      onClick={() => navigateActiveTab(link.url)}
                      className="glass rounded-xl p-3.5 text-left hover:border-cyan-500/30 hover:bg-white/[0.04] transition-all group flex flex-col justify-between h-24"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-2xl">{link.icon}</span>
                        <ExternalLink className="w-3 h-3 text-zinc-600 group-hover:text-cyan-400 transition-colors" />
                      </div>
                      <div>
                        <div className={cn('text-xs font-semibold truncate', link.color || 'text-zinc-200')}>
                          {link.label}
                        </div>
                        <div className="text-[10px] text-zinc-500 truncate">{link.desc}</div>
                      </div>
                    </button>
                  ))}
                </div>
              </div>

              {/* AI Research Prompts */}
              <div>
                <h2 className="text-xs font-mono uppercase tracking-widest text-zinc-500 mb-3 flex items-center gap-1.5">
                  <Sparkles className="w-3.5 h-3.5 text-amber-400" /> AI Browse Suggestions
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                  {AI_SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      onClick={() => navigateActiveTab(suggestion)}
                      className="glass rounded-xl px-4 py-3 text-left hover:border-cyan-500/30 hover:bg-white/[0.04] transition-all group flex items-center gap-3"
                    >
                      <Search className="w-4 h-4 text-zinc-500 group-hover:text-cyan-400 transition-colors shrink-0" />
                      <span className="text-xs text-zinc-300 group-hover:text-white transition-colors truncate">
                        {suggestion}
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Desktop Windows Reference (External OS Context) */}
              {desktopObservedTabs.length > 0 && (
                <div>
                  <h2 className="text-xs font-mono uppercase tracking-widest text-zinc-500 mb-3 flex items-center gap-1.5">
                    <Globe className="w-3.5 h-3.5 text-indigo-400" /> Detected Desktop Windows
                  </h2>
                  <div className="space-y-1.5">
                    {desktopObservedTabs.slice(0, 4).map((tab, idx) => (
                      <div
                        key={idx}
                        className="glass rounded-lg px-3 py-2 flex items-center justify-between text-xs"
                      >
                        <div className="flex items-center gap-2 truncate">
                          <span className="text-zinc-500 font-mono text-[10px]">{tab.browser || 'App'}</span>
                          <span className="text-zinc-300 truncate">{tab.title}</span>
                        </div>
                        <span className="text-[10px] text-zinc-600 font-mono shrink-0">OS Window</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            /* ── Active Web Content Area ── */
            <div className="flex-1 flex flex-col h-full overflow-hidden">
              {/* Web Header Bar */}
              <div className="flex items-center justify-between px-4 py-1.5 bg-[#101014] border-b border-white/[0.06] text-xs text-zinc-400 shrink-0">
                <div className="flex items-center gap-2 truncate max-w-md">
                  <span className="font-mono text-cyan-400 text-[11px] truncate">{activeTab.title}</span>
                  <span className="text-zinc-600">•</span>
                  <span className="text-zinc-500 text-[11px] truncate">{activeTab.url}</span>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <button
                    type="button"
                    onClick={() => handleOpenNative(activeTab.url)}
                    className="flex items-center gap-1 px-2 py-0.5 rounded-md bg-cyan-500/10 border border-cyan-500/25 text-[11px] text-cyan-400 hover:bg-cyan-500/20 transition-colors"
                  >
                    <Globe className="w-3 h-3" />
                    Open Native
                  </button>
                  <a
                    href={activeTab.url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-1 text-[11px] text-zinc-400 hover:text-zinc-200 transition-colors"
                  >
                    External <ExternalLink className="w-3 h-3" />
                  </a>
                </div>
              </div>

              {/* Sandboxed Embedded Iframe */}
              <div className="flex-1 bg-black p-1 relative">
                <iframe
                  key={activeTab.key}
                  src={iframeSrc}
                  title={activeTab.title}
                  sandbox="allow-scripts allow-forms allow-popups allow-modals"
                  onLoad={() => {
                    setTabs((prev) =>
                      prev.map((t) => (t.id === activeTab.id ? { ...t, isLoading: false } : t))
                    )
                  }}
                  className="w-full h-full rounded-lg border border-white/[0.08] bg-white"
                />
              </div>
            </div>
          )}
        </div>

        {/* ── AI Assistant Drawer ── */}
        <AnimatePresence>
          {aiDrawerOpen && (
            <motion.div
              initial={{ x: 360, opacity: 0 }}
              animate={{ x: 0, opacity: 1 }}
              exit={{ x: 360, opacity: 0 }}
              transition={{ type: 'spring', damping: 25, stiffness: 220 }}
              className="w-80 sm:w-96 border-l border-white/[0.08] bg-[#0d0d11] p-4 flex flex-col h-full shrink-0 shadow-2xl z-20"
            >
              {/* Drawer Header */}
              <div className="flex items-center justify-between pb-3 border-b border-white/[0.08] mb-4">
                <div className="flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-cyan-400" />
                  <h3 className="text-sm font-semibold text-zinc-100">AI Page Assistant</h3>
                </div>
                <button
                  onClick={() => setAiDrawerOpen(false)}
                  className="p-1 rounded-lg text-zinc-500 hover:text-zinc-200 hover:bg-white/[0.06]"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              {/* Drawer Content */}
              <div className="flex-1 overflow-y-auto space-y-4 pr-1">
                {/* Notice on Untrusted Content */}
                <div className="p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/20 text-[11px] text-amber-300 flex items-start gap-2">
                  <AlertCircle className="w-4 h-4 shrink-0 text-amber-400 mt-0.5" />
                  <span>
                    Web content is isolated as untrusted third-party information. Instructions found on pages are never executed automatically.
                  </span>
                </div>

                {/* Primary Action: Summarize Page */}
                <button
                  onClick={() => handleGenerateSummary()}
                  disabled={!activeTab.url || isAiLoading}
                  className={cn(
                    'w-full flex items-center justify-center gap-2 py-2 px-3 rounded-xl font-medium text-xs transition-all shadow-md',
                    activeTab.url && !isAiLoading
                      ? 'bg-gradient-to-r from-cyan-500 to-blue-600 text-white hover:from-cyan-400 hover:to-blue-500 shadow-cyan-500/20'
                      : 'bg-white/[0.05] text-zinc-500 cursor-not-allowed border border-white/[0.06]'
                  )}
                >
                  <Brain className={cn('w-4 h-4', isAiLoading && 'animate-spin')} />
                  {isAiLoading ? 'Analyzing Webpage...' : 'Summarize Current Page'}
                </button>

                {/* Ask a Question on the Page */}
                <div className="space-y-1.5">
                  <label className="text-[11px] font-medium text-zinc-400">Ask about this page</label>
                  <div className="flex gap-1.5">
                    <input
                      type="text"
                      placeholder="e.g. What are the key findings?"
                      value={aiQuestion}
                      onChange={(e) => setAiQuestion(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && aiQuestion.trim()) {
                          handleGenerateSummary(aiQuestion.trim())
                        }
                      }}
                      className="flex-1 bg-black/40 border border-white/[0.08] rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 placeholder-zinc-600 outline-none focus:border-cyan-500/40"
                    />
                    <button
                      onClick={() => handleGenerateSummary(aiQuestion.trim())}
                      disabled={!aiQuestion.trim() || !activeTab.url || isAiLoading}
                      className="px-2.5 py-1.5 bg-white/[0.06] hover:bg-cyan-500/20 text-zinc-300 hover:text-cyan-400 border border-white/[0.08] rounded-lg text-xs font-medium transition-colors disabled:opacity-40"
                    >
                      Ask
                    </button>
                  </div>
                </div>

                {/* Error Banner */}
                {aiError && (
                  <div className="p-2.5 rounded-lg bg-rose-500/10 border border-rose-500/20 text-xs text-rose-300">
                    {aiError}
                  </div>
                )}

                {/* AI Summary Results */}
                {aiSummaryData && (
                  <div className="space-y-3 pt-2">
                    <div className="p-3.5 rounded-xl bg-white/[0.03] border border-white/[0.08] space-y-2.5">
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] font-mono text-cyan-400 uppercase tracking-wider">
                          Synthesis
                        </span>
                        <div className="flex items-center gap-1.5">
                          <button
                            onClick={() => copyToClipboard(aiSummaryData.summary)}
                            className="p-1 rounded text-zinc-500 hover:text-zinc-200 hover:bg-white/[0.06] transition-colors"
                            title="Copy Summary"
                          >
                            {copiedSummary ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                          </button>
                        </div>
                      </div>

                      <div className="text-xs text-zinc-300 leading-relaxed whitespace-pre-wrap">
                        {aiSummaryData.summary}
                      </div>

                      {aiSummaryData.untrusted_content_notice && (
                        <div className="text-[10px] text-zinc-600 italic border-t border-white/[0.04] pt-2">
                          {aiSummaryData.untrusted_content_notice}
                        </div>
                      )}
                    </div>

                    {/* Save to HELIX Warm Memory Action */}
                    <button
                      onClick={handleSaveToMemory}
                      disabled={isMemorySaved}
                      className={cn(
                        'w-full flex items-center justify-center gap-2 py-2 px-3 rounded-xl text-xs font-medium transition-all border',
                        isMemorySaved
                          ? 'bg-emerald-500/15 border-emerald-500/30 text-emerald-400 cursor-default'
                          : 'bg-white/[0.04] border-white/[0.08] text-zinc-300 hover:border-cyan-500/30 hover:text-cyan-300 hover:bg-cyan-500/10'
                      )}
                    >
                      {isMemorySaved ? (
                        <>
                          <Check className="w-3.5 h-3.5" />
                          Saved to HELIX Memory
                        </>
                      ) : (
                        <>
                          <Brain className="w-3.5 h-3.5 text-cyan-400" />
                          Save Synthesis to Memory
                        </>
                      )}
                    </button>
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
