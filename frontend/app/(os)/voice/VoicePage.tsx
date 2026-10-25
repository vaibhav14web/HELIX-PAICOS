'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Mic, MicOff, Volume2, VolumeX, Zap, Loader2, AlertTriangle, ShieldAlert, Play } from 'lucide-react'
import { GlassCard } from '@/components/os/GlassCard'
import { useVoice } from '@/lib/hooks/use-voice'
import { usePermissions } from '@/lib/hooks/use-permissions'
import { useOSStore } from '@/lib/os-store'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { cleanTextForSpeech } from '@/lib/speech-cleaner'
import { playbackCoordinator, usePlaybackState } from '@/lib/voice/playback-coordinator'

type VoiceState = 'idle' | 'listening' | 'processing' | 'speaking' | 'interrupted' | 'permission_pending'

interface TranscriptEntry {
  time: string
  speaker: 'User' | 'HELIX'
  text: string
}

const stateConfig: Record<VoiceState, { label: string; color: string; ringColor: string; icon?: React.ComponentType<{ className?: string }> }> = {
  idle: { label: 'Ready to Listen', color: 'text-zinc-500', ringColor: 'rgba(255,255,255,0.06)' },
  listening: { label: 'Listening...', color: 'text-[#00d4ff]', ringColor: 'rgba(0,212,255,0.3)' },
  processing: { label: 'Processing...', color: 'text-[#7c3aed]', ringColor: 'rgba(124,58,237,0.3)', icon: Loader2 },
  speaking: { label: 'HELIX Speaking', color: 'text-[#22c55e]', ringColor: 'rgba(34,197,94,0.3)' },
  interrupted: { label: 'Interrupted', color: 'text-[#ef4444]', ringColor: 'rgba(239,68,68,0.3)', icon: AlertTriangle },
  permission_pending: { label: 'Permission Pending', color: 'text-[#f59e0b]', ringColor: 'rgba(245,158,11,0.3)', icon: ShieldAlert },
}

function WaveformBar({ delay, active }: { delay: number; active: boolean }) {
  return (
    <motion.div
      className="w-1 rounded-full bg-[#00d4ff]"
      animate={active ? { scaleY: [0.2, 1, 0.4, 0.8, 0.2], opacity: [0.5, 1, 0.7, 1, 0.5] } : { scaleY: 0.2, opacity: 0.2 }}
      transition={active ? { duration: 0.8, repeat: Infinity, delay, ease: 'easeInOut' } : { duration: 0.3 }}
      style={{ height: 40, transformOrigin: 'center' }}
    />
  )
}

function WaveformVisualizer({ active, bars = 32 }: { active: boolean; bars?: number }) {
  return (
    <div className="flex items-center gap-0.5" style={{ height: 40 }}>
      {Array.from({ length: bars }, (_, i) => (
        <WaveformBar key={i} delay={(i / bars) * 0.6} active={active} />
      ))}
    </div>
  )
}

const defaultVoiceProfiles = [
  { id: 'aria', name: 'ARIA', desc: 'Professional · English US', locale: 'en-US', speed: 1.0, pitch: 1.0, is_default: true, asset_available: true },
  { id: 'nova', name: 'NOVA', desc: 'Warm · English UK', locale: 'en-GB', speed: 0.95, pitch: 1.05, is_default: false, asset_available: true },
  { id: 'orion', name: 'ORION', desc: 'Deep · English US', locale: 'en-US', speed: 0.90, pitch: 0.85, is_default: false, asset_available: true },
]

const shortcuts = [
  { key: 'Space', label: 'Hold to Talk' },
  { key: '⌘ +', label: 'Increase Volume' },
  { key: '⌘ −', label: 'Decrease Volume' },
  { key: 'Esc', label: 'Cancel' },
]

export function VoicePage() {
  const { activeSessionId } = useOSStore()
  const { speak, command, profiles: profilesQuery, selectProfile, previewProfile } = useVoice(activeSessionId)
  const { pending } = usePermissions()
  const pendingCount = pending.data?.pending?.length ?? 0
  const { isSpeaking } = usePlaybackState()

  const [voiceState, setVoiceState] = useState<VoiceState>('idle')
  const [muted, setMuted] = useState(false)
  const [volume, setVolume] = useState(80)
  const [transcript, setTranscript] = useState('')
  const [transcripts, setTranscripts] = useState<TranscriptEntry[]>([])
  const [previewingProfileId, setPreviewingProfileId] = useState<string | null>(null)
  const [selectedOptimisticId, setSelectedOptimisticId] = useState<string | null>(null)
  const mediaRecorder = useRef<MediaRecorder | null>(null)
  const audioChunks = useRef<Blob[]>([])

  const activePersonalityId = selectedOptimisticId || profilesQuery.data?.active_personality_id || profilesQuery.data?.active_profile || profilesQuery.data?.active_profile_id || 'aria'
  const activeProfileId = activePersonalityId
  const currentProfiles = (profilesQuery.data?.profiles && profilesQuery.data.profiles.length > 0)
    ? profilesQuery.data.profiles
    : defaultVoiceProfiles

  // Authoritative Playback State Synchronization:
  // When assistant audio is playing, speaking indicator activates.
  // When audio finishes naturally (or errors/stops), indicator automatically returns to idle.
  useEffect(() => {
    if (isSpeaking) {
      if (voiceState !== 'listening') {
        setVoiceState('speaking')
      }
    } else {
      if (voiceState === 'speaking') {
        setVoiceState('idle')
      }
    }
  }, [isSpeaking, voiceState])

  const handleInterrupt = useCallback(() => {
    playbackCoordinator.stop('interrupt')
    api.voice.interrupt().catch(() => {})
    setVoiceState('interrupted')
    setTranscript('Interrupted by user')
    setTimeout(() => {
      setVoiceState((curr) => (curr === 'interrupted' ? 'idle' : curr))
      setTranscript('')
    }, 1200)
  }, [])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && (voiceState === 'speaking' || isSpeaking)) {
        handleInterrupt()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [voiceState, isSpeaking, handleInterrupt])

  // Track permission pending transitions
  useEffect(() => {
    if (pendingCount > 0 && voiceState === 'idle') {
      setVoiceState('permission_pending')
    } else if (pendingCount === 0 && voiceState === 'permission_pending') {
      setVoiceState('idle')
    }
  }, [pendingCount, voiceState])

  const audioContextRef = useRef<AudioContext | null>(null)
  const animFrameRef = useRef<number | null>(null)
  const maxTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  const stopAudioCapture = useCallback(() => {
    if (maxTimeoutRef.current) {
      clearTimeout(maxTimeoutRef.current)
      maxTimeoutRef.current = null
    }
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current)
      animFrameRef.current = null
    }
    if (audioContextRef.current) {
      try {
        audioContextRef.current.close()
      } catch {}
      audioContextRef.current = null
    }
  }, [])

  useEffect(() => {
    return () => {
      stopAudioCapture()
      playbackCoordinator.stop('cancel')
    }
  }, [stopAudioCapture])

  const handleSelectProfile = (profileId: string) => {
    setSelectedOptimisticId(profileId)
    selectProfile.mutate(
      { profileId, sessionId: activeSessionId },
      { onSettled: () => setSelectedOptimisticId(null) }
    )
  }

  const handlePreview = async (profileId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setPreviewingProfileId(profileId)
    try {
      const res = await previewProfile.mutateAsync(profileId)
      if (res.audio) {
        await playbackCoordinator.play({
          audioId: res.audio_id || undefined,
          audioBase64: res.audio,
          source: 'preview',
          onEnd: () => setPreviewingProfileId(null),
          onError: () => setPreviewingProfileId(null),
          onStop: () => setPreviewingProfileId(null),
        })
      } else if (res.preview_text) {
        const cleaned = cleanTextForSpeech(res.preview_text)
        const profile = currentProfiles.find((p) => p.id === profileId)
        const played = playbackCoordinator.speakUtterance(cleaned, {
          locale: profile?.locale,
          speed: profile?.speed,
          pitch: profile?.pitch,
          onEnd: () => setPreviewingProfileId(null),
          onError: () => setPreviewingProfileId(null),
          onStop: () => setPreviewingProfileId(null),
        })
        if (!played) setPreviewingProfileId(null)
      } else {
        setPreviewingProfileId(null)
      }
    } catch {
      setPreviewingProfileId(null)
    }
  }

  const toggle = useCallback(async () => {
    if (voiceState === 'speaking' || isSpeaking) {
      handleInterrupt()
      return
    }

    if (voiceState === 'listening') {
      // User manually stopped listening -> finish and process immediately
      stopAudioCapture()
      if (mediaRecorder.current && mediaRecorder.current.state === 'recording') {
        setVoiceState('processing')
        mediaRecorder.current.stop()
      }
      return
    }

    if (voiceState === 'idle' || voiceState === 'permission_pending') {
      stopAudioCapture()
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' })
        mediaRecorder.current = recorder
        audioChunks.current = []

        recorder.ondataavailable = (e) => {
          if (e.data.size > 0) audioChunks.current.push(e.data)
        }

        recorder.onstop = async () => {
          stopAudioCapture()
          stream.getTracks().forEach((t) => t.stop())
          const blob = new Blob(audioChunks.current, { type: 'audio/webm' })
          if (blob.size === 0) {
            setVoiceState('idle')
            return
          }

          setVoiceState('processing')
          try {
            const result = await command.mutateAsync({ file: blob, sessionId: activeSessionId })
            setTranscripts((prev) => [
              ...prev,
              { time: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false }), speaker: 'User', text: result.text || 'Voice command sent' },
            ])

            const playVoiceFallback = (text: string) => {
              const cleaned = cleanTextForSpeech(text)
              const activeProfile = currentProfiles.find((p) => p.id === activeProfileId)
              const played = playbackCoordinator.speakUtterance(cleaned, {
                locale: activeProfile?.locale,
                speed: activeProfile?.speed,
                pitch: activeProfile?.pitch,
                onError: () => setVoiceState('idle'),
              })
              if (!played) {
                setVoiceState('idle')
              }
            }

            if (result.response) {
              setTranscripts((prev) => [
                ...prev,
                { time: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false }), speaker: 'HELIX', text: result.response },
              ])
            }

            if (result.audio) {
              const playedOrQueued = await playbackCoordinator.play({
                audioId: result.audio_id || undefined,
                audioBase64: result.audio,
                source: 'http',
                onError: () => {
                  if (result.response) {
                    playVoiceFallback(result.response)
                  } else {
                    setVoiceState('idle')
                  }
                },
              })
              // If rejected as an already completed duplicate and not currently speaking, ensure idle
              if (!playedOrQueued && !playbackCoordinator.isSpeaking()) {
                setVoiceState('idle')
              }
            } else if (result.response) {
              playVoiceFallback(result.response)
            } else {
              setVoiceState('idle')
            }
          } catch {
            setVoiceState('idle')
          }
        }

        playbackCoordinator.stop()
        recorder.start()
        setVoiceState('listening')
        setTranscript('')

        // VAD: Speech Activity & Silence detection
        try {
          const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)()
          audioContextRef.current = audioCtx
          const source = audioCtx.createMediaStreamSource(stream)
          const analyser = audioCtx.createAnalyser()
          analyser.fftSize = 512
          source.connect(analyser)

          const dataArray = new Float32Array(analyser.fftSize)
          let speechDetected = false
          let silenceStartTime: number | null = null
          const listenStartTime = Date.now()

          const checkVolume = () => {
            if (!mediaRecorder.current || mediaRecorder.current.state !== 'recording') return
            analyser.getFloatTimeDomainData(dataArray)
            let sumSquares = 0
            for (let i = 0; i < dataArray.length; i++) {
              sumSquares += dataArray[i] * dataArray[i]
            }
            const rms = Math.sqrt(sumSquares / dataArray.length)

            const now = Date.now()
            if (rms > 0.012) {
              speechDetected = true
              silenceStartTime = null
            } else {
              if (speechDetected) {
                if (!silenceStartTime) {
                  silenceStartTime = now
                } else if (now - silenceStartTime > 2200) {
                  // User finished speaking, detected 2.2s silence
                  stopAudioCapture()
                  if (mediaRecorder.current?.state === 'recording') {
                    setVoiceState('processing')
                    mediaRecorder.current.stop()
                  }
                  return
                }
              } else if (now - listenStartTime > 12000) {
                // No speech detected after 12 seconds
                stopAudioCapture()
                if (mediaRecorder.current?.state === 'recording') {
                  mediaRecorder.current.stop()
                }
                return
              }
            }

            animFrameRef.current = requestAnimationFrame(checkVolume)
          }

          animFrameRef.current = requestAnimationFrame(checkVolume)
        } catch {}

        maxTimeoutRef.current = setTimeout(() => {
          stopAudioCapture()
          if (mediaRecorder.current?.state === 'recording') {
            setVoiceState('processing')
            mediaRecorder.current.stop()
          }
        }, 35000)
      } catch {
        setVoiceState('idle')
      }
    }
  }, [voiceState, isSpeaking, currentProfiles, activeProfileId, command, handleInterrupt, stopAudioCapture, activeSessionId])

  const cfg = stateConfig[voiceState]
  const Icon = cfg.icon

  return (
    <div className="flex h-full">
      {/* Main voice area */}
      <div className="flex-1 flex flex-col items-center justify-center gap-8 p-8">
        <motion.div
          key={voiceState}
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          className={cn('flex items-center gap-2 text-sm font-mono', cfg.color)}
        >
          {Icon && <Icon className={cn("w-4 h-4", voiceState === 'processing' && "animate-spin")} />}
          {voiceState === 'listening' && (
            <motion.span className="w-2 h-2 rounded-full bg-[#00d4ff] animate-pulse-glow" />
          )}
          {cfg.label}
        </motion.div>

        {/* Central orb */}
        <div className="relative flex items-center justify-center">
          {[80, 60, 40].map((size, i) => (
            <motion.div
              key={i}
              className="absolute rounded-full border"
              style={{ width: 140 + size * 2, height: 140 + size * 2, borderColor: cfg.ringColor }}
              animate={voiceState !== 'idle' ? { scale: [1, 1.04, 1], opacity: [0.4, 0.15, 0.4] } : { scale: 1, opacity: 0.1 }}
              transition={{ duration: 2, repeat: Infinity, delay: i * 0.4 }}
            />
          ))}

          <motion.button
            onClick={toggle}
            whileHover={{ scale: 1.04 }}
            whileTap={{ scale: 0.96 }}
            className={cn(
              'relative w-36 h-36 rounded-full flex items-center justify-center transition-all',
              voiceState !== 'idle'
                ? 'bg-[rgba(0,212,255,0.15)] border-2 border-[#00d4ff] glow-cyan'
                : 'glass border border-[rgba(255,255,255,0.1)] hover:border-[rgba(0,212,255,0.3)]',
            )}
          >
            <AnimatePresence mode="wait">
              {voiceState === 'idle' ? (
                <motion.div key="idle" initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.8, opacity: 0 }}>
                  <Mic className="w-12 h-12 text-zinc-400" />
                </motion.div>
              ) : voiceState === 'listening' ? (
                <motion.div key="listen" initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.8, opacity: 0 }}>
                  <Mic className="w-12 h-12 text-[#00d4ff] drop-shadow-[0_0_12px_rgba(0,212,255,0.8)]" />
                </motion.div>
              ) : voiceState === 'processing' ? (
                <motion.div key="proc" initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.8, opacity: 0 }}>
                  <Zap className="w-12 h-12 text-[#7c3aed] drop-shadow-[0_0_12px_rgba(124,58,237,0.8)]" />
                </motion.div>
              ) : (
                <motion.div key="speak" initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.8, opacity: 0 }}>
                  <Volume2 className="w-12 h-12 text-[#22c55e] drop-shadow-[0_0_12px_rgba(34,197,94,0.8)]" />
                </motion.div>
              )}
            </AnimatePresence>
          </motion.button>
        </div>

        <WaveformVisualizer active={voiceState === 'listening' || voiceState === 'speaking'} />

        <AnimatePresence>
          {transcript && (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="max-w-md text-center"
            >
              <GlassCard className="text-sm text-zinc-300 italic leading-relaxed">
                &ldquo;{transcript}&rdquo;
              </GlassCard>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="flex items-center gap-3">
          <button onClick={() => setMuted((v) => !v)} className="text-zinc-600 hover:text-zinc-300 transition-colors">
            {muted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
          </button>
          <input
            type="range" min={0} max={100} value={volume}
            onChange={(e) => setVolume(Number(e.target.value))}
            className="w-32 accent-[#00d4ff]"
          />
          <span className="text-xs font-mono text-zinc-600 w-8">{volume}%</span>
        </div>

        {/* Central Quick Voice Switcher */}
        <div className="flex items-center gap-1.5 p-1 px-2.5 rounded-full bg-[rgba(255,255,255,0.03)] border border-[rgba(255,255,255,0.08)] shadow-inner">
          <span className="text-[10px] font-mono uppercase tracking-wider text-zinc-500 font-semibold pl-1">Personality:</span>
          {currentProfiles.map((p) => {
            const isActive = p.id === activeProfileId
            const displayName = p.name || p.id || 'Voice'
            return (
              <button
                key={p.id}
                onClick={() => handleSelectProfile(p.id)}
                className={cn(
                  'px-3 py-1 rounded-full text-xs font-mono font-medium transition-all flex items-center gap-1.5',
                  isActive
                    ? 'bg-[rgba(0,212,255,0.18)] text-[#00d4ff] border border-[rgba(0,212,255,0.45)] shadow-[0_0_12px_rgba(0,212,255,0.25)]'
                    : 'text-zinc-400 hover:text-zinc-200 hover:bg-[rgba(255,255,255,0.06)] border border-transparent'
                )}
              >
                {isActive && <span className="w-1.5 h-1.5 rounded-full bg-[#00d4ff] animate-pulse-glow" />}
                {displayName}
              </button>
            )
          })}
        </div>

        <p className="text-xs text-zinc-600 font-mono">
          {voiceState === 'idle' ? 'Click the microphone to begin' : 'Click again to stop'}
        </p>
      </div>

      {/* Right panel */}
      <div className="w-72 shrink-0 border-l border-[rgba(255,255,255,0.06)] flex flex-col">
        <div className="p-4 border-b border-[rgba(255,255,255,0.06)]">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-mono uppercase tracking-widest text-zinc-500 font-semibold">Voice Profiles</h3>
            <span className="text-[10px] font-mono text-[#00d4ff] bg-[#00d4ff]/10 border border-[#00d4ff]/20 px-2 py-0.5 rounded-full">
              {currentProfiles.length} Available
            </span>
          </div>
          <div className="space-y-2">
            {currentProfiles.map((p) => {
              const isActive = p.id === activeProfileId
              const isPreviewing = previewingProfileId === p.id
              const descText = (p as any).desc || (p as any).description || 'English voice profile'
              const displayName = p.name || p.id || 'Voice'
              const initial = displayName[0]?.toUpperCase() || 'V'
              return (
                <div
                  key={p.id}
                  onClick={() => handleSelectProfile(p.id)}
                  className={cn(
                    'w-full flex items-center justify-between p-2.5 rounded-lg transition-all cursor-pointer group',
                    isActive
                      ? 'bg-[rgba(0,212,255,0.1)] border border-[rgba(0,212,255,0.35)] shadow-[0_0_15px_rgba(0,212,255,0.08)]'
                      : 'hover:bg-[rgba(255,255,255,0.04)] border border-[rgba(255,255,255,0.05)]',
                  )}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div className={cn(
                      'w-8 h-8 shrink-0 rounded-lg flex items-center justify-center text-xs font-bold font-mono transition-colors',
                      isActive ? 'bg-[rgba(0,212,255,0.2)] text-[#00d4ff] border border-[rgba(0,212,255,0.4)]' : 'bg-[rgba(255,255,255,0.05)] text-zinc-500'
                    )}>
                      {initial}
                    </div>
                    <div className="text-left truncate">
                      <div className="flex items-center gap-1.5">
                        <span className={cn('text-sm font-semibold', isActive ? 'text-[#00d4ff]' : 'text-zinc-200')}>
                          {displayName}
                        </span>
                        {isActive ? (
                          <span className="text-[9px] font-mono font-bold px-1.5 py-0.2 rounded bg-[#00d4ff]/20 text-[#00d4ff] border border-[#00d4ff]/30">
                            ACTIVE
                          </span>
                        ) : null}
                      </div>
                      <div className="text-xs text-zinc-400 truncate">{descText}</div>
                      <div className="text-[10px] text-zinc-500 font-mono mt-0.5">
                        {p.locale || 'en-US'} · Rate: {p.speed || 1.0}x
                      </div>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={(e) => handlePreview(p.id, e)}
                    disabled={isPreviewing}
                    title={`Audition ${displayName}'s voice`}
                    className={cn(
                      'p-1.5 rounded hover:bg-[rgba(255,255,255,0.1)] transition-colors shrink-0 ml-2 border',
                      isPreviewing
                        ? 'text-[#00d4ff] border-[#00d4ff]/40 bg-[#00d4ff]/10 animate-pulse'
                        : 'text-zinc-400 hover:text-zinc-100 border-[rgba(255,255,255,0.1)]'
                    )}
                  >
                    {isPreviewing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                  </button>
                </div>
              )
            })}
          </div>
        </div>

        <div className="flex-1 p-4 overflow-y-auto">
          <h3 className="text-xs font-mono uppercase tracking-widest text-zinc-600 mb-3">Session Log</h3>
          <div className="space-y-3">
            {transcripts.length === 0 && (
              <p className="text-xs text-zinc-700">No voice interactions yet</p>
            )}
            {transcripts.map((t, i) => (
              <div key={i} className="space-y-0.5">
                <div className="flex items-center gap-2">
                  <span className={cn('text-[10px] font-mono', t.speaker === 'HELIX' ? 'text-[#00d4ff]' : 'text-zinc-500')}>{t.speaker}</span>
                  <span className="text-[10px] text-zinc-700 font-mono">{t.time}</span>
                </div>
                <p className="text-xs text-zinc-400 leading-relaxed">{t.text}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="p-4 border-t border-[rgba(255,255,255,0.06)]">
          <h3 className="text-xs font-mono uppercase tracking-widest text-zinc-600 mb-3">Shortcuts</h3>
          <div className="space-y-2">
            {shortcuts.map((s) => (
              <div key={s.key} className="flex items-center justify-between">
                <span className="text-xs text-zinc-600">{s.label}</span>
                <kbd className="text-[10px] font-mono bg-[rgba(255,255,255,0.05)] text-zinc-500 px-1.5 py-0.5 rounded border border-[rgba(255,255,255,0.08)]">{s.key}</kbd>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
