'use client'

import { useEffect, useState } from 'react'
import { GlassCard } from '@/components/os/GlassCard'
import { Settings, Bell, Shield, Palette, Keyboard, Puzzle, User, ChevronRight, Check, Terminal, Trash2, Plus, ShieldCheck, ShieldAlert, X, Clock, Mic, Play, Loader2 } from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { usePermissions } from '@/lib/hooks/use-permissions'
import { useVoice } from '@/lib/hooks/use-voice'
import { cleanTextForSpeech } from '@/lib/speech-cleaner'
import { playbackCoordinator } from '@/lib/voice/playback-coordinator'

interface SettingsState {
  autoStart: boolean
  rememberHistory: boolean
  animations: boolean
  compactMode: boolean
  soundAlerts: boolean
  privateMode: boolean
  shortcutHints: boolean
  apiKeySaved: boolean
  browser_history_enabled?: boolean
}

const sections = [
  { id: 'general', label: 'General', icon: Settings, desc: 'Language, region, startup behavior' },
  { id: 'voice', label: 'Voice Personality', icon: Mic, desc: 'ARIA, NOVA, and ORION voice engine' },
  { id: 'appearance', label: 'Appearance', icon: Palette, desc: 'Theme, density, animations' },
  { id: 'notifications', label: 'Notifications', icon: Bell, desc: 'Alert preferences, sounds' },
  { id: 'privacy', label: 'Privacy & Security', icon: Shield, desc: 'Permissions, encryption, data controls' },
  { id: 'shortcuts', label: 'Keyboard Shortcuts', icon: Keyboard, desc: 'Custom key bindings' },
  { id: 'aliases', label: 'App Aliases', icon: Terminal, desc: 'Executable & app alias mappings' },
  { id: 'account', label: 'Account', icon: User, desc: 'Profile, billing, API keys' },
  { id: 'extensions', label: 'Extensions', icon: Puzzle, desc: 'Plugins and integrations' },
]

const defaultSettings: SettingsState = {
  autoStart: true,
  rememberHistory: true,
  animations: true,
  compactMode: false,
  soundAlerts: true,
  privateMode: false,
  shortcutHints: true,
  apiKeySaved: true,
  browser_history_enabled: false,
}

function ToggleRow({ label, description, enabled, onToggle }: { label: string; description: string; enabled: boolean; onToggle: () => void }) {
  return (
    <button onClick={onToggle} className="flex items-center justify-between rounded-xl border border-[rgba(255,255,255,0.07)] bg-[rgba(255,255,255,0.03)] px-3 py-3 text-left transition-colors hover:border-[rgba(0,212,255,0.2)]">
      <div>
        <div className="text-sm text-zinc-200">{label}</div>
        <div className="text-xs text-zinc-500 mt-0.5">{description}</div>
      </div>
      <div className={cn('flex h-6 w-11 items-center rounded-full px-1 transition-colors', enabled ? 'bg-[rgba(0,212,255,0.2)]' : 'bg-[rgba(255,255,255,0.08)]')}>
        <div className={cn('h-4 w-4 rounded-full bg-white transition-transform', enabled ? 'translate-x-5' : 'translate-x-0')} />
      </div>
    </button>
  )
}

function AppAliasesSection() {
  const [aliases, setAliases] = useState<Record<string, string>>({})
  const [newAlias, setNewAlias] = useState('')
  const [newExec, setNewExec] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchAliases = async () => {
    try {
      const res = await api.aliases.list()
      setAliases(res.aliases || {})
    } catch (e) {
      console.warn('Failed to load aliases', e)
    }
  }

  useEffect(() => {
    fetchAliases()
  }, [])

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newAlias.trim() || !newExec.trim()) return
    setLoading(true)
    setError(null)
    try {
      await api.aliases.register(newAlias.trim(), newExec.trim())
      setNewAlias('')
      setNewExec('')
      await fetchAliases()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to register alias')
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async (aliasKey: string) => {
    try {
      await api.aliases.delete(aliasKey)
      setAliases((prev) => {
        const next = { ...prev }
        delete next[aliasKey]
        return next
      })
    } catch (e) {
      console.warn('Failed to delete alias', e)
    }
  }

  return (
    <GlassCard className="space-y-4 p-5">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-medium text-zinc-100">Application Executable Aliases</div>
          <div className="text-xs text-zinc-500 mt-0.5">Map app nicknames to executable names or file paths</div>
        </div>
        <span className="text-xs font-mono text-[#00d4ff] bg-[#00d4ff]/10 border border-[#00d4ff]/20 px-2 py-0.5 rounded-full">
          {Object.keys(aliases).length} configured
        </span>
      </div>

      <form onSubmit={handleRegister} className="flex gap-2">
        <input
          type="text"
          placeholder="Alias (e.g. myeditor)"
          value={newAlias}
          onChange={(e) => setNewAlias(e.target.value)}
          className="flex-1 bg-[rgba(0,0,0,0.3)] border border-[rgba(255,255,255,0.1)] rounded-xl px-3 py-2 text-xs text-zinc-200 focus:outline-none focus:border-[#00d4ff]/40"
        />
        <input
          type="text"
          placeholder="Executable (e.g. notepad.exe)"
          value={newExec}
          onChange={(e) => setNewExec(e.target.value)}
          className="flex-1 bg-[rgba(0,0,0,0.3)] border border-[rgba(255,255,255,0.1)] rounded-xl px-3 py-2 text-xs text-zinc-200 focus:outline-none focus:border-[#00d4ff]/40"
        />
        <button
          type="submit"
          disabled={loading || !newAlias.trim() || !newExec.trim()}
          className="bg-[#00d4ff]/20 hover:bg-[#00d4ff]/30 text-[#00d4ff] border border-[#00d4ff]/40 rounded-xl px-3 py-2 text-xs flex items-center gap-1 transition-colors disabled:opacity-50"
        >
          <Plus className="w-3.5 h-3.5" /> Save
        </button>
      </form>

      {error && <div className="text-xs text-red-400">{error}</div>}

      <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
        {Object.entries(aliases).map(([alias, exe]) => (
          <div key={alias} className="flex items-center justify-between p-2.5 rounded-xl border border-[rgba(255,255,255,0.06)] bg-[rgba(255,255,255,0.02)]">
            <div className="flex items-center space-x-3">
              <Terminal className="w-4 h-4 text-[#00d4ff]" />
              <div>
                <span className="text-xs font-semibold text-zinc-200 font-mono">{alias}</span>
                <span className="text-xs text-zinc-500 ml-2 font-mono">→ {exe}</span>
              </div>
            </div>
            <button
              onClick={() => handleDelete(alias)}
              className="text-zinc-500 hover:text-red-400 p-1 rounded-lg hover:bg-white/5 transition-colors"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>
    </GlassCard>
  )
}

function PermissionsManagerSection() {
  const { history, pending, patterns, grant, deny, addAutoApprove, removeAutoApprove } = usePermissions()
  const [newPattern, setNewPattern] = useState('')

  const handleAddPattern = (e: React.FormEvent) => {
    e.preventDefault()
    if (!newPattern.trim()) return
    addAutoApprove.mutate(newPattern.trim())
    setNewPattern('')
  }

  const pendingList = pending.data?.pending || []
  const historyList = history.data?.history || []
  const patternsList = patterns.data?.patterns || []

  return (
    <div className="space-y-4">
      {/* Pending Permissions */}
      <GlassCard className="p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 text-[#f59e0b]" />
            <span className="text-sm font-medium text-zinc-100">Pending Approvals</span>
          </div>
          <span className="text-xs font-mono text-[#f59e0b] bg-[#f59e0b]/10 border border-[#f59e0b]/20 px-2 py-0.5 rounded-full">
            {pendingList.length} pending
          </span>
        </div>

        {pendingList.length === 0 ? (
          <div className="text-xs text-zinc-500 italic py-2">
            No pending permission requests at this time.
          </div>
        ) : (
          <div className="space-y-2.5">
            {pendingList.map((req) => (
              <div key={req.id} className="p-3 rounded-xl border border-[rgba(245,158,11,0.2)] bg-[rgba(245,158,11,0.03)] space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-zinc-200 font-mono">{req.action}</span>
                  <span className="text-[10px] text-zinc-400 font-mono">MODULE: {req.source_module}</span>
                </div>
                {req.reasoning && (
                  <p className="text-xs text-zinc-400">{req.reasoning}</p>
                )}
                <div className="flex items-center justify-end gap-2 pt-1">
                  <button
                    onClick={() => deny.mutate(req.id)}
                    className="flex items-center gap-1 px-3 py-1 text-xs rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 transition-colors"
                  >
                    <X className="w-3 h-3" /> Deny
                  </button>
                  <button
                    onClick={() => grant.mutate(req.id)}
                    className="flex items-center gap-1 px-3 py-1 text-xs rounded-lg bg-[#00d4ff]/20 hover:bg-[#00d4ff]/30 text-[#00d4ff] border border-[#00d4ff]/30 font-semibold transition-colors"
                  >
                    <Check className="w-3 h-3" /> Grant
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </GlassCard>

      {/* Auto-Approve Rules */}
      <GlassCard className="p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-[#22c55e]" />
            <span className="text-sm font-medium text-zinc-100">Auto-Approve Patterns</span>
          </div>
          <span className="text-xs font-mono text-[#22c55e] bg-[#22c55e]/10 border border-[#22c55e]/20 px-2 py-0.5 rounded-full">
            {patternsList.length} active
          </span>
        </div>
        <p className="text-xs text-zinc-500">
          Actions matching these wildcard patterns will be executed automatically without prompting.
        </p>

        <form onSubmit={handleAddPattern} className="flex gap-2">
          <input
            type="text"
            placeholder="Pattern (e.g. launch_application, git *, read_*)"
            value={newPattern}
            onChange={(e) => setNewPattern(e.target.value)}
            className="flex-1 bg-[rgba(0,0,0,0.3)] border border-[rgba(255,255,255,0.1)] rounded-xl px-3 py-2 text-xs text-zinc-200 focus:outline-none focus:border-[#22c55e]/40 font-mono"
          />
          <button
            type="submit"
            disabled={!newPattern.trim()}
            className="bg-[#22c55e]/20 hover:bg-[#22c55e]/30 text-[#22c55e] border border-[#22c55e]/40 rounded-xl px-3 py-2 text-xs flex items-center gap-1 transition-colors disabled:opacity-50"
          >
            <Plus className="w-3.5 h-3.5" /> Add Rule
          </button>
        </form>

        <div className="space-y-1.5 max-h-40 overflow-y-auto">
          {patternsList.map((pattern) => (
            <div key={pattern} className="flex items-center justify-between p-2 rounded-lg border border-white/5 bg-white/[0.02]">
              <span className="text-xs font-mono text-zinc-300">{pattern}</span>
              <button
                onClick={() => removeAutoApprove.mutate(pattern)}
                className="text-zinc-500 hover:text-red-400 p-1 rounded hover:bg-white/5 transition-colors"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
          {patternsList.length === 0 && (
            <div className="text-xs text-zinc-600 italic py-1">No custom auto-approve rules set.</div>
          )}
        </div>
      </GlassCard>

      {/* Permission History */}
      <GlassCard className="p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-zinc-400" />
            <span className="text-sm font-medium text-zinc-100">Permission History</span>
          </div>
          <span className="text-xs font-mono text-zinc-400 bg-white/5 border border-white/10 px-2 py-0.5 rounded-full">
            {historyList.length} logged
          </span>
        </div>

        <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
          {historyList.slice(-15).reverse().map((req, idx) => (
            <div key={idx} className="p-2.5 rounded-xl border border-white/5 bg-white/[0.02] flex items-center justify-between gap-3 text-xs">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-zinc-200 font-semibold">{req.action}</span>
                  <span className={cn(
                    'text-[10px] font-mono px-1.5 py-0.2 rounded',
                    req.status === 'approved' ? 'text-[#22c55e] bg-[#22c55e]/10' :
                    req.status === 'denied' ? 'text-red-400 bg-red-400/10' :
                    'text-[#00d4ff] bg-[#00d4ff]/10'
                  )}>
                    {req.status}
                  </span>
                </div>
                {req.reasoning && (
                  <p className="text-[11px] text-zinc-500 truncate mt-0.5">{req.reasoning}</p>
                )}
              </div>
              <span className="text-[10px] text-zinc-600 font-mono shrink-0">
                {req.decided_at ? new Date(req.decided_at).toLocaleTimeString() : (req.created_at ? new Date(req.created_at).toLocaleTimeString() : '')}
              </span>
            </div>
          ))}
          {historyList.length === 0 && (
            <div className="text-xs text-zinc-600 italic py-1">No historical permission requests.</div>
          )}
        </div>
      </GlassCard>
    </div>
  )
}

function VoiceSettingsSection() {
  const { profiles: profilesQuery, selectProfile, previewProfile } = useVoice()
  const [previewingId, setPreviewingId] = useState<string | null>(null)
  const [selectedOptimisticId, setSelectedOptimisticId] = useState<string | null>(null)

  const activePersonalityId = selectedOptimisticId || profilesQuery.data?.active_personality_id || profilesQuery.data?.active_profile || profilesQuery.data?.active_profile_id || 'aria'
  const activeId = activePersonalityId
  const profiles = (profilesQuery.data?.profiles && profilesQuery.data.profiles.length > 0)
    ? profilesQuery.data.profiles
    : [
        { id: 'aria', name: 'ARIA', desc: 'Professional · English US', locale: 'en-US', speed: 1.0, pitch: 1.0 },
        { id: 'nova', name: 'NOVA', desc: 'Warm · English UK', locale: 'en-GB', speed: 0.95, pitch: 1.05 },
        { id: 'orion', name: 'ORION', desc: 'Deep · English US', locale: 'en-US', speed: 0.90, pitch: 0.85 },
      ]

  const handleSelect = (id: string) => {
    setSelectedOptimisticId(id)
    selectProfile.mutate({ profileId: id }, { onSettled: () => setSelectedOptimisticId(null) })
  }

  const handlePreview = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setPreviewingId(id)
    try {
      const res = await previewProfile.mutateAsync(id)
      if (res.audio) {
        await playbackCoordinator.play({
          audioId: res.audio_id || undefined,
          audioBase64: res.audio,
          source: 'preview',
          onEnd: () => setPreviewingId(null),
          onError: () => setPreviewingId(null),
        })
      } else if (res.text) {
        const cleaned = cleanTextForSpeech(res.text)
        if (typeof window !== 'undefined' && 'speechSynthesis' in window && cleaned) {
          window.speechSynthesis.cancel()
          const utterance = new SpeechSynthesisUtterance(cleaned)
          const target = profiles.find((p) => p.id === id)
          if (target) {
            utterance.lang = target.locale || 'en-US'
            utterance.rate = target.speed || 1.0
            utterance.pitch = target.pitch || 1.0
          }
          utterance.onend = () => setPreviewingId(null)
          utterance.onerror = () => setPreviewingId(null)
          window.speechSynthesis.speak(utterance)
        } else {
          setPreviewingId(null)
        }
      } else {
        setPreviewingId(null)
      }
    } catch {
      setPreviewingId(null)
    }
  }

  return (
    <div className="space-y-4">
      <GlassCard className="p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Mic className="w-4 h-4 text-[#00d4ff]" />
            <span className="text-sm font-medium text-zinc-100">Voice Personalities</span>
          </div>
          <span className="text-xs font-mono text-[#00d4ff] bg-[#00d4ff]/10 border border-[#00d4ff]/20 px-2.5 py-0.5 rounded-full">
            {profiles.length} Available
          </span>
        </div>
        <p className="text-xs text-zinc-400">
          Select an authoritative voice identity for HELIX. Your selection controls all spoken responses, conversational cadence, and audio alerts across sessions.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 pt-2">
          {profiles.map((p) => {
            const isActive = p.id === activeId
            const isPreviewing = previewingId === p.id
            const desc = (p as any).desc || (p as any).description || 'English voice profile'
            return (
              <div
                key={p.id}
                onClick={() => handleSelect(p.id)}
                className={cn(
                  'p-3.5 rounded-xl border transition-all cursor-pointer flex flex-col justify-between gap-3 group relative overflow-hidden',
                  isActive
                    ? 'bg-[rgba(0,212,255,0.1)] border-[#00d4ff]/50 shadow-[0_0_20px_rgba(0,212,255,0.15)]'
                    : 'bg-[rgba(255,255,255,0.02)] border-[rgba(255,255,255,0.08)] hover:border-[rgba(0,212,255,0.3)] hover:bg-[rgba(255,255,255,0.04)]'
                )}
              >
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-1.5">
                      <div className={cn(
                        'w-6 h-6 rounded-md flex items-center justify-center text-xs font-bold font-mono',
                        isActive ? 'bg-[#00d4ff]/20 text-[#00d4ff]' : 'bg-[rgba(255,255,255,0.06)] text-zinc-400'
                      )}>
                        {p.name[0]}
                      </div>
                      <span className={cn('text-sm font-semibold', isActive ? 'text-[#00d4ff]' : 'text-zinc-200')}>
                        {p.name}
                      </span>
                    </div>
                    {isActive && (
                      <span className="text-[9px] font-mono font-bold px-1.5 py-0.5 rounded bg-[#00d4ff]/20 text-[#00d4ff] border border-[#00d4ff]/30">
                        ACTIVE
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-zinc-400 leading-relaxed">{desc}</p>
                  <p className="text-[10px] text-zinc-500 font-mono mt-1">
                    {p.locale || 'en-US'} · Speed: {p.speed || 1.0}x
                  </p>
                </div>

                <div className="pt-2 border-t border-[rgba(255,255,255,0.06)] flex items-center justify-between">
                  <button
                    type="button"
                    onClick={(e) => handlePreview(p.id, e)}
                    disabled={isPreviewing}
                    className={cn(
                      'px-2.5 py-1 rounded-lg text-xs font-mono flex items-center gap-1.5 transition-colors border',
                      isPreviewing
                        ? 'bg-[#00d4ff]/20 text-[#00d4ff] border-[#00d4ff]/40 animate-pulse'
                        : 'bg-[rgba(255,255,255,0.05)] text-zinc-300 hover:text-zinc-100 hover:bg-[rgba(255,255,255,0.1)] border-[rgba(255,255,255,0.1)]'
                    )}
                  >
                    {isPreviewing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
                    Audition
                  </button>

                  <span className={cn('text-[11px] font-mono', isActive ? 'text-[#00d4ff] font-semibold' : 'text-zinc-500')}>
                    {isActive ? 'Current Voice' : 'Click to Activate'}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      </GlassCard>
    </div>
  )
}

export function SettingsPage() {
  const [active, setActive] = useState('general')
  const [settings, setSettings] = useState<SettingsState>(defaultSettings)

  useEffect(() => {
    const loadSettings = async () => {
      try {
        const res = await api.settings.get()
        if (res.settings) {
          setSettings((prev) => ({ ...prev, ...res.settings }))
        }
      } catch (e) {
        console.warn('Failed to fetch backend settings, using local fallback', e)
        const stored = window.localStorage.getItem('helix-settings')
        if (stored) {
          try {
            setSettings({ ...defaultSettings, ...JSON.parse(stored) })
          } catch {
            // ignore
          }
        }
      }
    }
    loadSettings()
  }, [])

  const updateSetting = async (key: keyof SettingsState) => {
    const nextVal = !settings[key]
    setSettings((prev) => ({ ...prev, [key]: nextVal }))
    window.localStorage.setItem('helix-settings', JSON.stringify({ ...settings, [key]: nextVal }))
    try {
      await api.settings.update({ [key]: nextVal })
    } catch (e) {
      console.warn('Failed to sync setting to backend', e)
    }
  }

  const content = () => {
    switch (active) {
      case 'voice':
        return <VoiceSettingsSection />
      case 'appearance':
        return (
          <div className="space-y-3">
            <ToggleRow label="Animations" description="Enable motion and transitions" enabled={settings.animations} onToggle={() => updateSetting('animations')} />
            <ToggleRow label="Compact mode" description="Reduce spacing for dense layouts" enabled={settings.compactMode} onToggle={() => updateSetting('compactMode')} />
          </div>
        )
      case 'notifications':
        return (
          <div className="space-y-3">
            <ToggleRow label="Sound alerts" description="Play sound for important notifications" enabled={settings.soundAlerts} onToggle={() => updateSetting('soundAlerts')} />
          </div>
        )
      case 'privacy':
        return (
          <div className="space-y-4">
            <ToggleRow label="Private mode" description="Limit context sharing to local sessions" enabled={settings.privateMode} onToggle={() => updateSetting('privateMode')} />
            <ToggleRow label="Browser history access" description="Allow HELIX to read Chrome and Edge history for contextual queries (disabled by default)" enabled={settings.browser_history_enabled ?? false} onToggle={() => updateSetting('browser_history_enabled')} />
            <PermissionsManagerSection />
          </div>
        )
      case 'shortcuts':
        return (
          <div className="space-y-3">
            <ToggleRow label="Shortcut hints" description="Show keyboard hints in the UI" enabled={settings.shortcutHints} onToggle={() => updateSetting('shortcutHints')} />
          </div>
        )
      case 'aliases':
        return <AppAliasesSection />
      case 'account':
        return (
          <div className="space-y-3">
            <ToggleRow label="Saved API key" description="Keep your API credentials available" enabled={settings.apiKeySaved} onToggle={() => updateSetting('apiKeySaved')} />
          </div>
        )
      case 'extensions':
        return (
          <GlassCard className="space-y-2">
            <div className="text-sm font-medium text-zinc-200">Connected extensions</div>
            <div className="flex items-center gap-2 rounded-lg border border-[rgba(34,197,94,0.2)] bg-[rgba(34,197,94,0.08)] px-3 py-2 text-sm text-[#22c55e]">
              <Check className="w-4 h-4" /> HELIX Browser Bridge
            </div>
            <div className="flex items-center gap-2 rounded-lg border border-[rgba(0,212,255,0.2)] bg-[rgba(0,212,255,0.08)] px-3 py-2 text-sm text-[#00d4ff]">
              <Check className="w-4 h-4" /> Local Context Sync
            </div>
          </GlassCard>
        )
      default:
        return (
          <div className="space-y-3">
            <ToggleRow label="Auto start" description="Launch HELIX on system startup" enabled={settings.autoStart} onToggle={() => updateSetting('autoStart')} />
            <ToggleRow label="Remember history" description="Keep conversation history across sessions" enabled={settings.rememberHistory} onToggle={() => updateSetting('rememberHistory')} />
          </div>
        )
    }
  }

  return (
    <div className="flex h-full">
      <div className="w-60 shrink-0 border-r border-[rgba(255,255,255,0.06)] p-4">
        <h2 className="text-xs font-mono uppercase tracking-widest text-zinc-600 mb-4">Settings</h2>
        <div className="space-y-1">
          {sections.map((s) => {
            const Icon = s.icon
            return (
              <button
                key={s.id}
                onClick={() => setActive(s.id)}
                className={cn(
                  'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-colors',
                  active === s.id ? 'bg-[rgba(0,212,255,0.08)] text-[#00d4ff]' : 'text-zinc-400 hover:text-zinc-200 hover:bg-[rgba(255,255,255,0.03)]',
                )}
              >
                <Icon className="w-4 h-4" />
                <div className="flex-1">
                  <div className="text-sm font-medium">{s.label}</div>
                  <div className="text-[10px] text-zinc-600">{s.desc}</div>
                </div>
                <ChevronRight className="w-3 h-3 text-zinc-700" />
              </button>
            )
          })}
        </div>
      </div>

      <div className="flex-1 p-6">
        <div className="max-w-2xl space-y-4">
          <div>
            <h3 className="text-lg font-semibold text-zinc-100">{sections.find((s) => s.id === active)?.label}</h3>
            <p className="text-sm text-zinc-500 mt-1">These controls are now stored locally in the browser so your choices persist.</p>
          </div>
          {content()}
        </div>
      </div>
    </div>
  )
}
