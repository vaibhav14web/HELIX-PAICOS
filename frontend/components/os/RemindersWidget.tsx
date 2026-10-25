'use client'

import { useEffect, useState } from 'react'
import { Bell, Plus, Trash2, Clock, CheckCircle2, AlertCircle } from 'lucide-react'
import { GlassCard } from '@/components/os/GlassCard'
import { api, type ReminderEntry } from '@/lib/api-client'
import { cn } from '@/lib/utils'

export function RemindersWidget() {
  const [reminders, setReminders] = useState<ReminderEntry[]>([])
  const [text, setText] = useState('')
  const [delayMinutes, setDelayMinutes] = useState('5')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchReminders = async () => {
    try {
      if (!api.reminders?.list) return
      const res = await api.reminders.list()
      setReminders(res.reminders || [])
    } catch (e) {
      console.warn('Failed to load reminders', e)
    }
  }

  useEffect(() => {
    fetchReminders()
    const interval = setInterval(fetchReminders, 5000)
    return () => clearInterval(interval)
  }, [])

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!text.trim()) return
    setLoading(true)
    setError(null)
    try {
      const mins = parseFloat(delayMinutes) || 5
      if (!api.reminders?.add) throw new Error('Reminders API not available')
      await api.reminders.add({
        text: text.trim(),
        delay_seconds: mins * 60,
      })
      setText('')
      await fetchReminders()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to add reminder')
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      if (!api.reminders?.delete) return
      await api.reminders.delete(id)
      setReminders((prev) => prev.filter((r) => r.id !== id))
    } catch (err) {
      console.warn('Failed to delete reminder', err)
    }
  }

  return (
    <GlassCard glow className="p-5 flex flex-col space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <Bell className="w-5 h-5 text-[#00d4ff]" />
          <h3 className="font-medium text-sm text-zinc-100">Timetable Reminders</h3>
        </div>
        <span className="text-xs font-mono text-zinc-400 bg-[rgba(255,255,255,0.05)] px-2 py-0.5 rounded-full border border-[rgba(255,255,255,0.08)]">
          {reminders.filter((r) => !r.triggered).length} Active
        </span>
      </div>

      <form onSubmit={handleAdd} className="flex gap-2">
        <input
          type="text"
          placeholder="Remind me to..."
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="flex-1 bg-[rgba(0,0,0,0.3)] border border-[rgba(255,255,255,0.1)] rounded-xl px-3 py-2 text-xs text-zinc-200 focus:outline-none focus:border-[#00d4ff]/40"
        />
        <select
          value={delayMinutes}
          onChange={(e) => setDelayMinutes(e.target.value)}
          className="bg-[rgba(0,0,0,0.3)] border border-[rgba(255,255,255,0.1)] rounded-xl px-2 py-2 text-xs text-zinc-300 focus:outline-none"
        >
          <option value="1">in 1 min</option>
          <option value="5">in 5 min</option>
          <option value="15">in 15 min</option>
          <option value="30">in 30 min</option>
          <option value="60">in 1 hour</option>
        </select>
        <button
          type="submit"
          disabled={loading || !text.trim()}
          className="bg-[#00d4ff]/20 hover:bg-[#00d4ff]/30 text-[#00d4ff] border border-[#00d4ff]/40 rounded-xl px-3 py-2 text-xs flex items-center gap-1 transition-colors disabled:opacity-50"
        >
          <Plus className="w-3.5 h-3.5" />
          Add
        </button>
      </form>

      {error && <div className="text-xs text-red-400">{error}</div>}

      <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
        {reminders.length === 0 ? (
          <div className="text-xs text-zinc-500 py-3 text-center">No scheduled reminders. Add one above or ask via voice.</div>
        ) : (
          reminders.map((r) => {
            const isDue = r.triggered
            const timeFormatted = r.timestamp ? new Date(r.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : r.time || 'Scheduled'

            return (
              <div
                key={r.id}
                className={cn(
                  'flex items-center justify-between p-3 rounded-xl border transition-colors',
                  isDue
                    ? 'bg-[rgba(34,197,94,0.05)] border-[rgba(34,197,94,0.2)]'
                    : 'bg-[rgba(255,255,255,0.02)] border-[rgba(255,255,255,0.06)] hover:border-[rgba(0,212,255,0.2)]'
                )}
              >
                <div className="flex items-center space-x-3">
                  {isDue ? (
                    <CheckCircle2 className="w-4 h-4 text-[#22c55e]" />
                  ) : (
                    <Clock className="w-4 h-4 text-[#00d4ff]" />
                  )}
                  <div>
                    <div className={cn('text-xs font-medium', isDue ? 'text-zinc-400 line-through' : 'text-zinc-200')}>{r.text}</div>
                    <div className="text-[10px] text-zinc-500 flex items-center space-x-1 mt-0.5">
                      <span>{timeFormatted}</span>
                      {r.recurring && <span className="bg-[#7c3aed]/20 text-[#7c3aed] px-1.5 rounded text-[9px]">recurring</span>}
                    </div>
                  </div>
                </div>
                <button
                  onClick={() => handleDelete(r.id)}
                  className="text-zinc-500 hover:text-red-400 p-1.5 rounded-lg hover:bg-white/5 transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            )
          })
        )}
      </div>
    </GlassCard>
  )
}
