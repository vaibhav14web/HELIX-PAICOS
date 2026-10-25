'use client'

import React, { useState, useEffect } from 'react'
import { Target, CheckCircle2, Circle, Clock, Plus, Layers, AlertCircle, Archive, Trash2, RotateCcw, AlertTriangle } from 'lucide-react'
import { api } from '@/lib/api-client'

export default function GoalsPage() {
  const [goals, setGoals] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [currentTab, setCurrentTab] = useState<'active' | 'archived'>('active')
  const [showModal, setShowModal] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [newPriority, setNewPriority] = useState('medium')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [goalToDelete, setGoalToDelete] = useState<any | null>(null)
  const [actionInProgress, setActionInProgress] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const loadGoals = async (tab: 'active' | 'archived') => {
    try {
      setLoading(true)
      const res = await api.goals.get({ archivedOnly: tab === 'archived' })
      setGoals(res?.goals || [])
    } catch (err) {
      console.error('Failed loading goals:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadGoals(currentTab)
    const interval = setInterval(() => {
      api.goals.get({ archivedOnly: currentTab === 'archived' })
        .then((res) => setGoals(res?.goals || []))
        .catch(() => {})
    }, 3000)
    const onFocus = () => loadGoals(currentTab)
    window.addEventListener('focus', onFocus)
    return () => {
      clearInterval(interval)
      window.removeEventListener('focus', onFocus)
    }
  }, [currentTab])

  const handleCreateGoal = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmedTitle = newTitle.trim()
    if (!trimmedTitle) return
    setError(null)
    setSubmitting(true)
    try {
      await api.goals.create({ title: trimmedTitle, description: newDesc.trim(), priority: newPriority })
      await loadGoals(currentTab)
      setShowModal(false)
      setNewTitle('')
      setNewDesc('')
    } catch (err: any) {
      console.error('Failed creating goal:', err)
      setError(err?.message || 'Failed to save goal. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  const handleArchiveGoal = async (goal: any) => {
    setActionError(null)
    setActionInProgress(goal.goal_id)
    try {
      await api.goals.archive(goal.goal_id)
      await loadGoals(currentTab)
    } catch (err: any) {
      console.error('Failed archiving goal:', err)
      setActionError(err?.message || 'Failed to archive goal.')
    } finally {
      setActionInProgress(null)
    }
  }

  const handleRestoreGoal = async (goal: any) => {
    setActionError(null)
    setActionInProgress(goal.goal_id)
    try {
      await api.goals.restore(goal.goal_id)
      await loadGoals(currentTab)
    } catch (err: any) {
      console.error('Failed restoring goal:', err)
      setActionError(err?.message || 'Failed to restore goal.')
    } finally {
      setActionInProgress(null)
    }
  }

  const handleConfirmDelete = async () => {
    if (!goalToDelete) return
    setActionError(null)
    setActionInProgress(goalToDelete.goal_id)
    try {
      await api.goals.delete(goalToDelete.goal_id)
      setGoalToDelete(null)
      await loadGoals(currentTab)
    } catch (err: any) {
      console.error('Failed deleting goal:', err)
      setActionError(err?.message || 'Failed to permanently delete goal.')
    } finally {
      setActionInProgress(null)
    }
  }

  if (loading && goals.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-400">
        <Target className="w-6 h-6 animate-spin mr-2 text-indigo-400" />
        <span>Loading Long-Running Goals...</span>
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto p-6 space-y-6 text-zinc-100 bg-zinc-950/40">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-zinc-800 pb-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-zinc-100 flex items-center gap-2">
            <Target className="w-7 h-7 text-emerald-400" />
            Long-Running Goals & Milestones
          </h1>
          <p className="text-xs text-zinc-400 mt-1">
            Track multi-step objective DAGs, subtask dependency progress, and persistent operational milestones.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => loadGoals(currentTab)}
            title="Refresh Goals"
            className="flex items-center gap-1.5 bg-zinc-850 hover:bg-zinc-800 text-zinc-300 px-3 py-2 rounded-xl text-xs font-semibold border border-zinc-700/60 hover:border-zinc-600 transition-all shadow-sm"
          >
            <RotateCcw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
          <button
            onClick={() => setShowModal(true)}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-xl text-xs font-semibold shadow-lg shadow-indigo-600/20 transition-all"
          >
            <Plus className="w-4 h-4" />
            <span>New Goal</span>
          </button>
        </div>
      </div>

      {/* Action Error Banner */}
      {actionError && (
        <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-xs flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{actionError}</span>
          </div>
          <button onClick={() => setActionError(null)} className="text-zinc-400 hover:text-zinc-200 text-xs">Dismiss</button>
        </div>
      )}

      {/* Tab Switcher */}
      <div className="flex items-center gap-2 border-b border-zinc-800/80 pb-2">
        <button
          onClick={() => setCurrentTab('active')}
          className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
            currentTab === 'active'
              ? 'bg-zinc-800 text-zinc-100 border border-zinc-700/80 shadow-sm'
              : 'text-zinc-400 hover:text-zinc-200'
          }`}
        >
          Active Goals
        </button>
        <button
          onClick={() => setCurrentTab('archived')}
          className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
            currentTab === 'archived'
              ? 'bg-zinc-800 text-zinc-100 border border-zinc-700/80 shadow-sm'
              : 'text-zinc-400 hover:text-zinc-200'
          }`}
        >
          Archived Goals
        </button>
      </div>

      {/* Empty State */}
      {goals.length === 0 ? (
        <div className="p-12 text-center rounded-2xl bg-zinc-900/30 border border-zinc-800/60 backdrop-blur-sm space-y-3">
          <Target className="w-8 h-8 text-zinc-600 mx-auto" />
          <h3 className="text-sm font-semibold text-zinc-300">
            {currentTab === 'active' ? 'No active goals found' : 'No archived goals found'}
          </h3>
          <p className="text-xs text-zinc-500 max-w-sm mx-auto">
            {currentTab === 'active'
              ? 'Create a new goal or ask HELIX in conversation to establish an operational milestone.'
              : 'Goals you archive will appear here. You can restore or permanently delete them anytime.'}
          </p>
        </div>
      ) : (
        /* Goals Canvas List */
        <div className="space-y-4">
          {goals.map((goal) => (
            <div key={goal.goal_id} className="p-5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 backdrop-blur-md space-y-4">
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-3">
                    <h3 className="text-base font-bold text-zinc-100">{goal.title}</h3>
                    <span className="px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                      {goal.priority} Priority
                    </span>
                    {goal.status === 'archived' && (
                      <span className="px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase bg-amber-500/10 text-amber-400 border border-amber-500/20">
                        Archived
                      </span>
                    )}
                  </div>
                  {goal.description ? <p className="text-xs text-zinc-400 mt-1">{goal.description}</p> : null}
                </div>
                <div className="flex items-center gap-4">
                  <div className="text-right">
                    <span className="text-xl font-extrabold text-indigo-400">{goal.progress_percent}%</span>
                    <span className="text-[10px] text-zinc-500 block">Progress</span>
                  </div>
                  {/* Actions */}
                  <div className="flex items-center gap-1.5 pl-2 border-l border-zinc-800">
                    {currentTab === 'active' ? (
                      <button
                        onClick={() => handleArchiveGoal(goal)}
                        disabled={actionInProgress === goal.goal_id}
                        title="Archive Goal"
                        className="p-1.5 rounded-lg bg-zinc-800/60 hover:bg-zinc-700/80 text-zinc-400 hover:text-zinc-200 border border-zinc-700/40 transition-colors disabled:opacity-50"
                      >
                        <Archive className="w-3.5 h-3.5" />
                      </button>
                    ) : (
                      <button
                        onClick={() => handleRestoreGoal(goal)}
                        disabled={actionInProgress === goal.goal_id}
                        title="Restore Goal"
                        className="p-1.5 rounded-lg bg-zinc-800/60 hover:bg-emerald-500/20 text-emerald-400 hover:text-emerald-300 border border-zinc-700/40 hover:border-emerald-500/30 transition-colors disabled:opacity-50"
                      >
                        <RotateCcw className="w-3.5 h-3.5" />
                      </button>
                    )}
                    <button
                      onClick={() => {
                        setActionError(null)
                        setGoalToDelete(goal)
                      }}
                      disabled={actionInProgress === goal.goal_id}
                      title="Permanently Delete Goal"
                      className="p-1.5 rounded-lg bg-zinc-800/60 hover:bg-red-500/20 text-zinc-400 hover:text-red-400 border border-zinc-700/40 hover:border-red-500/30 transition-colors disabled:opacity-50"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>

              {/* Progress Bar */}
              <div className="w-full bg-zinc-800/60 h-2 rounded-full overflow-hidden">
                <div
                  className="bg-gradient-to-r from-indigo-500 to-emerald-400 h-full transition-all duration-500"
                  style={{ width: `${goal.progress_percent}%` }}
                />
              </div>

              {/* Subtask DAG Nodes */}
              <div className="space-y-2 pt-2">
                <span className="text-xs font-semibold text-zinc-400 flex items-center gap-1.5">
                  <Layers className="w-3.5 h-3.5 text-zinc-500" /> Subtask Milestone Tree
                </span>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  {goal.tasks?.map((t: any) => (
                    <div key={t.task_id} className="p-3 bg-zinc-800/30 rounded-xl border border-zinc-800/60 flex items-center justify-between text-xs">
                      <div className="flex items-center gap-2">
                        {t.status === 'completed' ? (
                          <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                        ) : t.status === 'in_progress' ? (
                          <Clock className="w-4 h-4 text-indigo-400 animate-pulse" />
                        ) : (
                          <Circle className="w-4 h-4 text-zinc-600" />
                        )}
                        <span className={t.status === 'completed' ? 'line-through text-zinc-500' : 'text-zinc-200'}>
                          {t.title}
                        </span>
                      </div>
                      <span className="text-[10px] capitalize font-mono text-zinc-400">{t.status}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Permanent Deletion Confirmation Modal */}
      {goalToDelete && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6 w-full max-w-md space-y-4 shadow-2xl">
            <h2 className="text-lg font-bold text-zinc-100 flex items-center gap-2">
              <AlertTriangle className="w-5 h-5 text-red-400" /> Confirm Permanent Deletion
            </h2>
            <p className="text-xs text-zinc-300 leading-relaxed">
              Are you sure you want to permanently delete <strong className="text-zinc-100">"{goalToDelete.title}"</strong>?
              This action cannot be undone. All associated milestone DAGs, subtasks, and execution records will be permanently removed.
            </p>

            {actionError && (
              <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-xs flex items-center gap-2">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{actionError}</span>
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => {
                  if (actionInProgress) return
                  setGoalToDelete(null)
                  setActionError(null)
                }}
                disabled={!!actionInProgress}
                className="px-4 py-2 rounded-xl text-xs font-semibold text-zinc-400 hover:text-zinc-200 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmDelete}
                disabled={!!actionInProgress}
                className="px-4 py-2 rounded-xl text-xs font-semibold bg-red-600 hover:bg-red-500 text-white shadow-lg shadow-red-600/20 disabled:opacity-50 transition-all flex items-center gap-1.5"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>{actionInProgress ? 'Deleting...' : 'Permanently Delete'}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* New Goal Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <form onSubmit={handleCreateGoal} className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6 w-full max-w-md space-y-4">
            <h2 className="text-lg font-bold text-zinc-100 flex items-center gap-2">
              <Target className="w-5 h-5 text-indigo-400" /> Create Long-Running Goal
            </h2>

            {error && (
              <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-xs flex items-center gap-2">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1">Goal Title</label>
              <input
                type="text"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="e.g., English Interview"
                className="w-full bg-zinc-800/80 border border-zinc-700 rounded-xl p-2.5 text-xs text-zinc-100 focus:outline-none focus:border-indigo-500"
                required
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1">Description</label>
              <textarea
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                placeholder="Details of objectives and milestones..."
                className="w-full bg-zinc-800/80 border border-zinc-700 rounded-xl p-2.5 text-xs text-zinc-100 focus:outline-none focus:border-indigo-500 h-24"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => {
                  setShowModal(false)
                  setError(null)
                }}
                className="px-4 py-2 rounded-xl text-xs font-semibold text-zinc-400 hover:text-zinc-200"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="px-4 py-2 rounded-xl text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-50"
              >
                {submitting ? 'Creating...' : 'Create Goal'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}
