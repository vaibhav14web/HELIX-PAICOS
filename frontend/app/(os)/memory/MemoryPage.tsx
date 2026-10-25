'use client'

import { useDeferredValue, useEffect, useMemo, useState, type FormEvent } from 'react'
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useNodesState,
  type Node,
  BackgroundVariant,
} from 'reactflow'
import 'reactflow/dist/style.css'
import { StatusBadge } from '@/components/os/GlassCard'
import { Search, Database, Plus, Clock3, Loader2, AlertCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAddMemoryEntry, useMemoryEntries } from '@/lib/hooks/use-memory'
import { getMemoryViewState } from '@/lib/memory-view-model'
import type { WarmMemoryEntry } from '@/lib/api-client'

const categoryPalette = [
  { color: '#00d4ff', border: 'rgba(0,212,255,0.4)', bg: 'rgba(0,212,255,0.1)' },
  { color: '#22c55e', border: 'rgba(34,197,94,0.4)', bg: 'rgba(34,197,94,0.1)' },
  { color: '#7c3aed', border: 'rgba(124,58,237,0.4)', bg: 'rgba(124,58,237,0.1)' },
  { color: '#f59e0b', border: 'rgba(245,158,11,0.4)', bg: 'rgba(245,158,11,0.1)' },
  { color: '#ef4444', border: 'rgba(239,68,68,0.4)', bg: 'rgba(239,68,68,0.1)' },
]

function getCategoryConfig(category: string) {
  const hash = Array.from(category).reduce((value, char) => value + char.charCodeAt(0), 0)
  return categoryPalette[hash % categoryPalette.length]
}

function MemoryNode({ data }: { data: { label: string; category: string } }) {
  const cfg = getCategoryConfig(data.category)
  return (
    <div
      className="px-3 py-2 rounded-xl text-xs font-medium transition-all max-w-56"
      style={{
        background: cfg.bg,
        border: `1px solid ${cfg.border}`,
        color: cfg.color,
        minWidth: 120,
        boxShadow: `0 0 12px ${cfg.color}22`,
      }}
    >
      <div className="font-mono font-bold text-xs line-clamp-3" style={{ color: cfg.color }}>{data.label}</div>
      <div className="text-[10px] mt-1 opacity-60 capitalize">{data.category.replace(/[_-]+/g, ' ')}</div>
    </div>
  )
}

const nodeTypes = { memory: MemoryNode }

function toMemoryNode(entry: WarmMemoryEntry, index: number): Node {
  const angle = (index / Math.max(index + 1, 8)) * Math.PI * 2
  const radius = 90 + (index % 3) * 55
  const label = entry.text.replace(/\s+/g, ' ').trim()
  return {
    id: entry.id,
    type: 'memory',
    position: { x: 360 + Math.cos(angle) * radius, y: 260 + Math.sin(angle) * radius },
    data: { label: label.length > 120 ? `${label.slice(0, 117)}…` : label, category: entry.category || 'conversation' },
  }
}

export function MemoryPage() {
  const [activeFilter, setActiveFilter] = useState('All')
  const [searchQuery, setSearchQuery] = useState('')
  const [isAddNodeOpen, setIsAddNodeOpen] = useState(false)
  const [entryText, setEntryText] = useState('')
  const [entryCategory, setEntryCategory] = useState('conversation')
  const deferredSearch = useDeferredValue(searchQuery)
  const memory = useMemoryEntries(deferredSearch, activeFilter)
  const addMemory = useAddMemoryEntry()
  const [nodes, setNodes, onNodesChange] = useNodesState([])

  const mappedNodes = useMemo(() => memory.entries.map(toMemoryNode), [memory.entries])
  useEffect(() => {
    setNodes((prevNodes) => {
      if (
        prevNodes.length === mappedNodes.length &&
        prevNodes.every(
          (n, i) =>
            n.id === mappedNodes[i].id &&
            (n.data as { label?: string; category?: string })?.label === (mappedNodes[i].data as { label?: string; category?: string })?.label &&
            (n.data as { label?: string; category?: string })?.category === (mappedNodes[i].data as { label?: string; category?: string })?.category
        )
      ) {
        return prevNodes
      }
      return mappedNodes
    })
  }, [mappedNodes, setNodes])

  useEffect(() => {
    if (!isAddNodeOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsAddNodeOpen(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [isAddNodeOpen])

  const handleAddMemory = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const text = entryText.trim()
    if (!text) return
    try {
      await addMemory.mutateAsync({ text, category: entryCategory })
      setEntryText('')
      setEntryCategory('conversation')
      setIsAddNodeOpen(false)
    } catch {
      // The mutation error is shown in the form without exposing backend details.
    }
  }

  const filters = ['All', ...memory.categories]
  const status = memory.statsError
    ? 'error'
    : memory.serviceStatus === 'ready'
      ? 'online'
      : memory.serviceStatus === 'stopped'
        ? 'offline'
        : 'busy'
  const statusLabel = memory.statsError
    ? 'Memory status unavailable'
    : memory.serviceStatus === 'ready'
      ? 'Memory service ready'
      : memory.serviceStatus === 'stopped'
        ? 'Memory service stopped'
        : 'Checking memory service'
  const categoryCounts = memory.entries.reduce<Record<string, number>>((counts, entry) => {
    counts[entry.category] = (counts[entry.category] ?? 0) + 1
    return counts
  }, {})
  const memoryViewState = getMemoryViewState({
    isLoading: memory.isLoading,
    hasError: Boolean(memory.error),
    entryCount: memory.entries.length,
    searchText: searchQuery,
    category: activeFilter,
  })

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-6 py-4 border-b border-[rgba(255,255,255,0.06)] shrink-0">
        <div className="flex items-center gap-4 min-w-0">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-600" />
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search memory..."
              aria-label="Search memory"
              className="pl-8 pr-3 py-1.5 bg-[rgba(255,255,255,0.04)] border border-[rgba(255,255,255,0.07)] rounded-lg text-xs text-zinc-300 placeholder:text-zinc-600 outline-none focus:border-[rgba(0,212,255,0.3)] w-48 transition-colors"
            />
          </div>
          <div className="flex items-center gap-1 overflow-x-auto">
            {filters.map((filter) => (
              <button
                key={filter}
                onClick={() => setActiveFilter(filter)}
                className={cn(
                  'px-2.5 py-1 rounded-lg text-xs font-medium transition-colors capitalize whitespace-nowrap',
                  activeFilter === filter ? 'bg-[rgba(0,212,255,0.1)] text-[#00d4ff]' : 'text-zinc-600 hover:text-zinc-300',
                )}
              >
                {filter === 'All' ? filter : filter.replace(/[_-]+/g, ' ')}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={status} label={statusLabel} />
          <button
            onClick={() => {
              addMemory.reset()
              setIsAddNodeOpen(true)
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[rgba(0,212,255,0.08)] border border-[rgba(0,212,255,0.2)] text-[#00d4ff] text-xs font-medium hover:bg-[rgba(0,212,255,0.12)] transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
            Add Node
          </button>
        </div>
      </div>

      <div className="flex flex-1 min-h-0">
        <div className="flex-1 min-w-0 relative">
          {memoryViewState === 'error' ? (
            <div className="h-full flex flex-col items-center justify-center gap-3 px-6 text-center">
              <AlertCircle className="w-6 h-6 text-red-400" aria-hidden="true" />
              <div>
                <p className="text-sm text-zinc-200">Saved memories couldn’t be loaded.</p>
                <p className="mt-1 text-xs text-zinc-500">Check that the memory service is available, then try again.</p>
              </div>
              <button onClick={() => void memory.retry()} className="rounded-lg border border-white/10 px-3 py-2 text-xs text-zinc-300 hover:bg-white/[0.05]">
                Try again
              </button>
            </div>
          ) : memoryViewState === 'loading' ? (
            <div className="h-full flex flex-col items-center justify-center gap-3 text-zinc-400" role="status" aria-live="polite">
              <Loader2 className="w-5 h-5 animate-spin text-[#00d4ff]" aria-hidden="true" />
              <p className="text-sm">Loading saved memories…</p>
            </div>
          ) : memoryViewState !== 'results' ? (
            <div className="h-full flex flex-col items-center justify-center gap-3 px-6 text-center">
              <Database className="w-6 h-6 text-zinc-600" aria-hidden="true" />
              <div>
                <p className="text-sm text-zinc-300">
                  {searchQuery.trim()
                    ? 'No matching memories found.'
                    : activeFilter !== 'All'
                      ? `No memories in ${activeFilter.replace(/[_-]+/g, ' ')}.`
                      : 'No saved memories yet.'}
                </p>
                <p className="mt-1 text-xs text-zinc-500">
                  {searchQuery.trim() || activeFilter !== 'All'
                    ? 'Try another search or clear the current filters.'
                    : 'Add a memory and it will appear here.'}
                </p>
              </div>
              {searchQuery.trim() || activeFilter !== 'All' ? (
                <button
                  onClick={() => {
                    setSearchQuery('')
                    setActiveFilter('All')
                  }}
                  className="rounded-lg border border-white/10 px-3 py-2 text-xs text-zinc-300 hover:bg-white/[0.05]"
                >
                  Clear filters
                </button>
              ) : (
                <button
                  onClick={() => {
                    addMemory.reset()
                    setIsAddNodeOpen(true)
                  }}
                  className="rounded-lg border border-[rgba(0,212,255,0.2)] bg-[rgba(0,212,255,0.08)] px-3 py-2 text-xs text-[#00d4ff] hover:bg-[rgba(0,212,255,0.12)]"
                >
                  Add your first memory
                </button>
              )}
            </div>
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={[]}
              onNodesChange={onNodesChange}
              nodeTypes={nodeTypes}
              fitView
              fitViewOptions={{ padding: 0.2 }}
              className="bg-transparent"
            >
              <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="rgba(255,255,255,0.03)" />
              <Controls />
              <MiniMap nodeColor={(node) => getCategoryConfig((node.data as { category: string }).category).color} />
            </ReactFlow>
          )}
          {memory.error && memory.entries.length > 0 && (
            <div className="absolute left-3 right-3 top-3 z-10 flex items-center justify-between gap-3 rounded-lg border border-amber-500/20 bg-[#17181f]/95 px-3 py-2" role="status">
              <span className="text-xs text-amber-200">Couldn’t load more memories. Showing the entries already loaded.</span>
              <button onClick={() => void memory.retry()} className="shrink-0 text-xs text-zinc-300 underline underline-offset-2">Retry</button>
            </div>
          )}
        </div>

        <div className="w-64 shrink-0 border-l border-[rgba(255,255,255,0.06)] flex flex-col overflow-y-auto">
          <div className="p-4 border-b border-[rgba(255,255,255,0.06)]">
            <h3 className="text-xs font-mono uppercase tracking-widest text-zinc-600 mb-3">Memory Results</h3>
            <div className="grid grid-cols-2 gap-2">
              <div className="glass rounded-lg p-2.5">
                <Database className="w-3.5 h-3.5 mb-1 text-[#00d4ff]" />
                <div className="text-sm font-bold font-mono text-[#00d4ff]">{memory.isLoading && memory.entries.length === 0 ? '…' : memory.entries.length}</div>
                <div className="text-[10px] text-zinc-600 mt-0.5">Shown</div>
              </div>
              <div className="glass rounded-lg p-2.5">
                <Search className="w-3.5 h-3.5 mb-1 text-[#7c3aed]" />
                <div className="text-sm font-bold font-mono text-[#7c3aed]">
                  {memory.isStatsLoading ? '…' : memory.statsError ? '—' : memory.statsCount ?? '—'}
                </div>
                <div className="text-[10px] text-zinc-600 mt-0.5">Your memories</div>
              </div>
            </div>
            {memory.statsError && (
              <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-amber-300" role="status">
                <span>Total unavailable</span>
                <button onClick={() => void memory.retry()} className="underline underline-offset-2">Retry</button>
              </div>
            )}
            {memory.hasNextPage && !searchQuery && (
              <button
                onClick={() => void memory.fetchNextPage()}
                disabled={memory.isFetchingNextPage}
                className="mt-3 w-full rounded-lg border border-white/10 px-2 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 disabled:opacity-50"
              >
                {memory.isFetchingNextPage ? 'Loading…' : 'Load more memories'}
              </button>
            )}
          </div>

          <div className="p-4 border-b border-[rgba(255,255,255,0.06)]">
            <h3 className="text-xs font-mono uppercase tracking-widest text-zinc-600 mb-3">Categories</h3>
            <div className="space-y-2">
              {Object.entries(categoryCounts).map(([category, count]) => {
                const cfg = getCategoryConfig(category)
                return (
                  <div key={category} className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <div className="w-2 h-2 rounded-full shrink-0" style={{ background: cfg.color }} />
                      <span className="text-xs text-zinc-500 capitalize truncate">{category.replace(/[_-]+/g, ' ')}</span>
                    </div>
                    <span className="text-[10px] font-mono text-zinc-600">{count}</span>
                  </div>
                )
              })}
              {Object.keys(categoryCounts).length === 0 && <p className="text-xs text-zinc-600">No categories to show.</p>}
            </div>
          </div>

          <div className="p-4">
            <h3 className="text-xs font-mono uppercase tracking-widest text-zinc-600 mb-3">Recent Activity</h3>
            <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-4 text-center">
              <Clock3 className="w-4 h-4 mx-auto mb-2 text-zinc-600" aria-hidden="true" />
              <p className="text-xs text-zinc-400">No activity events available</p>
              <p className="mt-1 text-[10px] leading-relaxed text-zinc-600">
                Saved memory records appear in the graph. Activity history is not provided by the memory service yet.
              </p>
            </div>
          </div>
        </div>
      </div>

      {isAddNodeOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !addMemory.isPending) setIsAddNodeOpen(false)
          }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="add-memory-title"
            className="w-full max-w-lg rounded-2xl border border-white/10 bg-[#101116] p-5 shadow-2xl"
          >
            <div className="mb-5">
              <h2 id="add-memory-title" className="text-base font-semibold text-zinc-100">Add a memory</h2>
              <p className="mt-1 text-xs text-zinc-500">Save a note to your private HELIX memory.</p>
            </div>
            <form onSubmit={handleAddMemory} className="space-y-4">
              <label className="block space-y-1.5">
                <span className="text-xs font-medium text-zinc-400">Memory</span>
                <textarea
                  autoFocus
                  required
                  maxLength={10000}
                  value={entryText}
                  onChange={(event) => setEntryText(event.target.value)}
                  placeholder="What should HELIX remember?"
                  rows={5}
                  className="w-full resize-y rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 outline-none focus:border-[rgba(0,212,255,0.4)]"
                />
              </label>
              <label className="block space-y-1.5">
                <span className="text-xs font-medium text-zinc-400">Category</span>
                <select
                  value={entryCategory}
                  onChange={(event) => setEntryCategory(event.target.value)}
                  className="w-full rounded-lg border border-white/10 bg-[#17181f] px-3 py-2 text-sm text-zinc-200 outline-none focus:border-[rgba(0,212,255,0.4)]"
                >
                  {Array.from(new Set(['conversation', ...memory.categories])).map((category) => (
                    <option key={category} value={category}>{category.replace(/[_-]+/g, ' ')}</option>
                  ))}
                </select>
              </label>
              {addMemory.isError && <p role="alert" className="text-xs text-red-400">Could not save this memory. Please try again.</p>}
              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setIsAddNodeOpen(false)}
                  disabled={addMemory.isPending}
                  className="rounded-lg border border-white/10 px-3 py-2 text-xs text-zinc-400 hover:text-zinc-200 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={!entryText.trim() || addMemory.isPending}
                  className="rounded-lg bg-[rgba(0,212,255,0.12)] px-3 py-2 text-xs font-medium text-[#00d4ff] hover:bg-[rgba(0,212,255,0.18)] disabled:opacity-50"
                >
                  {addMemory.isPending ? 'Saving…' : 'Save memory'}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </div>
  )
}
