/**
 * frontend/lib/voice/playback-coordinator.ts
 *
 * Centralized Audio Playback Coordinator for HELIX.
 * Enforces:
 * 1. Exactly-once playback per audio segment / response ID across both WebSocket and HTTP streams.
 * 2. Single active audio playback invariant across all views (Chat, Voice, Settings, OSShell).
 * 3. Robust lifecycle transitions:
 *    - Natural completion: idle → playing → idle
 *    - Interruption: idle → playing → stopped → idle
 *    - Failure / error: idle → playing → error → idle
 * 4. Micro-echo and microphone loopback suppression.
 * 5. Monotonic sequence tagging preventing stale completion events from corrupting newer responses.
 * 6. Multi-chunk audio queue support maintaining active speaking state until final chunk completes.
 * 7. Authoritative state ownership with reactive React integration via useSyncExternalStore.
 */

import { useSyncExternalStore } from 'react'

export type PlaybackStatus = 'idle' | 'generating' | 'playing' | 'stopped' | 'error'

export interface PlaybackState {
  status: PlaybackStatus
  isSpeaking: boolean
  isGeneratingSpeech: boolean
  hasQueuedAudio: boolean
  activeAudioId: string | null
  activeResponseId: string | null
}

export interface PlaybackOptions {
  audioId?: string
  responseId?: string
  chunkIndex?: number
  isFinalChunk?: boolean
  audioBase64: string
  source?: 'ws' | 'http' | 'preview' | 'system'
  onStart?: () => void
  onEnd?: () => void
  onError?: (err: any) => void
  onStop?: () => void
}

interface ActiveCallListeners {
  onStart?: () => void
  onEnd?: () => void
  onError?: (err: any) => void
  onStop?: () => void
}

class PlaybackCoordinator {
  private currentAudio: HTMLAudioElement | null = null
  private currentAudioId: string | null = null
  private currentResponseId: string | null = null
  private currentSequence: number = 0
  private playedAudioIds: Set<string> = new Set()
  private maxHistorySize = 300

  private activeListeners: ActiveCallListeners[] = []
  private audioQueue: PlaybackOptions[] = []

  private status: PlaybackStatus = 'idle'
  private isGenerating: boolean = false
  private subscribers: Set<() => void> = new Set()

  // Cached immutable snapshot for useSyncExternalStore
  private cachedSnapshot: PlaybackState = {
    status: 'idle',
    isSpeaking: false,
    isGeneratingSpeech: false,
    hasQueuedAudio: false,
    activeAudioId: null,
    activeResponseId: null,
  }

  constructor() {
    if (typeof window !== 'undefined') {
      ;(window as any).__helix_playback_coordinator = this
    }
  }

  public getState(): PlaybackState {
    return this.cachedSnapshot
  }

  public isSpeaking(): boolean {
    return this.cachedSnapshot.isSpeaking
  }

  public getStatus(): PlaybackStatus {
    return this.status
  }

  public subscribe(listener: () => void): () => void {
    this.subscribers.add(listener)
    return () => {
      this.subscribers.delete(listener)
    }
  }

  private updateState(newStatus: PlaybackStatus) {
    this.status = newStatus
    const isSpeaking = newStatus === 'playing'
    const hasQueuedAudio = this.audioQueue.length > 0

    this.cachedSnapshot = {
      status: newStatus,
      isSpeaking,
      isGeneratingSpeech: this.isGenerating,
      hasQueuedAudio,
      activeAudioId: this.currentAudioId,
      activeResponseId: this.currentResponseId,
    }

    if (typeof window !== 'undefined') {
      window.dispatchEvent(
        new CustomEvent('helix:playback_state', {
          detail: { ...this.cachedSnapshot },
        })
      )
      if (isSpeaking) {
        window.dispatchEvent(
          new CustomEvent('helix:playback_start', {
            detail: { audioId: this.currentAudioId, responseId: this.currentResponseId },
          })
        )
      } else {
        window.dispatchEvent(
          new CustomEvent('helix:playback_end', {
            detail: { audioId: this.currentAudioId, responseId: this.currentResponseId },
          })
        )
      }
    }

    this.subscribers.forEach((sub) => {
      try {
        sub()
      } catch (err) {
        console.error('[PlaybackCoordinator] Subscriber notification error:', err)
      }
    })
  }

  public setGeneratingSpeech(isGenerating: boolean) {
    this.isGenerating = isGenerating
    if (this.status === 'idle' && isGenerating) {
      this.updateState('generating')
    } else if (this.status === 'generating' && !isGenerating) {
      this.updateState('idle')
    } else {
      this.updateState(this.status)
    }
  }

  public hasPlayed(audioId: string): boolean {
    if (!audioId) return false
    return this.playedAudioIds.has(audioId)
  }

  private computeFingerprint(audioBase64: string): string {
    const len = audioBase64.length
    const sample = audioBase64.slice(0, 80)
    return `fp_${len}_${sample}`
  }

  /**
   * Enqueue or immediately play an audio segment with exactly-once guarantee.
   * Returns true if audio playback was initiated or queued, false if rejected as a duplicate.
   */
  public async play(options: PlaybackOptions): Promise<boolean> {
    const { audioId, responseId, audioBase64, source = 'system', onStart, onEnd, onError, onStop } = options

    if (!audioBase64) {
      return false
    }

    const effectiveId = audioId || this.computeFingerprint(audioBase64)

    // Deduplication check
    if (this.playedAudioIds.has(effectiveId)) {
      // If this exact audioId is CURRENTLY playing (e.g. WS started it, and HTTP now returned),
      // attach the HTTP caller's callbacks to the active playback so it gets naturally notified on completion.
      if (this.currentAudioId === effectiveId && this.status === 'playing') {
        this.activeListeners.push({ onStart, onEnd, onError, onStop })
      }
      if (process.env.NODE_ENV !== 'production') {
        console.info(`[PlaybackCoordinator] Deduplicated audio segment (${source}): ${effectiveId}`)
      }
      return false
    }

    // Mark as played immediately to guard against concurrent delivery race conditions
    this.playedAudioIds.add(effectiveId)
    if (this.playedAudioIds.size > this.maxHistorySize) {
      const oldest = Array.from(this.playedAudioIds).slice(0, 50)
      oldest.forEach((id) => this.playedAudioIds.delete(id))
    }

    // If another audio response is currently playing, stop it first (Single Active Invariant)
    if (this.status === 'playing' || this.currentAudio) {
      this.stopInternal('interrupt', false)
    }

    return this.executePlay(options, effectiveId)
  }

  private async executePlay(options: PlaybackOptions, effectiveId: string): Promise<boolean> {
    const { responseId, audioBase64, source = 'system', onStart, onEnd, onError, onStop, isFinalChunk = true } = options

    const seq = ++this.currentSequence
    this.currentAudioId = effectiveId
    this.currentResponseId = responseId || null
    this.activeListeners = [{ onStart, onEnd, onError, onStop }]

    let audio: HTMLAudioElement | null = null
    try {
      const audioUrl = audioBase64.startsWith('data:')
        ? audioBase64
        : `data:audio/wav;base64,${audioBase64}`

      audio = new Audio(audioUrl)
      this.currentAudio = audio

      if (typeof window !== 'undefined') {
        ;(window as any).__helix_current_audio = audio
      }

      const notifyEnd = () => {
        const listeners = [...this.activeListeners]
        this.activeListeners = []
        listeners.forEach((l) => {
          try {
            l.onEnd?.()
          } catch (e) {
            console.error('[PlaybackCoordinator] onEnd callback error:', e)
          }
        })
      }

      const notifyError = (err: any) => {
        const listeners = [...this.activeListeners]
        this.activeListeners = []
        listeners.forEach((l) => {
          try {
            l.onError?.(err)
          } catch (e) {
            console.error('[PlaybackCoordinator] onError callback error:', e)
          }
        })
      }

      const handleChunkCompletion = () => {
        // Discard stale completion events from older audio instances
        if (this.currentSequence !== seq) {
          return
        }

        // Clean up current audio element
        this.releaseAudioElement(audio)

        // Check if there are queued chunks remaining for this response
        if (this.audioQueue.length > 0) {
          const nextChunk = this.audioQueue.shift()!
          const nextEffectiveId = nextChunk.audioId || this.computeFingerprint(nextChunk.audioBase64)
          this.executePlay(nextChunk, nextEffectiveId)
          return
        }

        // Playback has completely and naturally finished: playing → idle
        this.currentAudio = null
        this.currentAudioId = null
        this.currentResponseId = null
        this.updateState('idle')
        notifyEnd()
      }

      audio.onplay = () => {
        if (this.currentSequence !== seq) return
        this.updateState('playing')
        this.activeListeners.forEach((l) => {
          try {
            l.onStart?.()
          } catch (e) {
            console.error('[PlaybackCoordinator] onStart callback error:', e)
          }
        })
      }

      audio.onended = () => {
        handleChunkCompletion()
      }

      audio.onerror = (err) => {
        if (this.currentSequence !== seq) return
        console.warn(`[PlaybackCoordinator] Audio error (${source}):`, err)
        this.releaseAudioElement(audio)
        this.currentAudio = null
        this.currentAudioId = null
        this.currentResponseId = null
        // Error transition: playing → error → idle
        this.updateState('error')
        notifyError(err)
        setTimeout(() => {
          if (this.currentSequence === seq && this.status === 'error') {
            this.updateState('idle')
          }
        }, 50)
      }

      // Initial state transition to playing
      this.updateState('playing')
      await audio.play()
      return true
    } catch (err) {
      if (this.currentSequence !== seq) return false
      console.warn(`[PlaybackCoordinator] Playback rejected/blocked (${source}):`, err)
      if (audio && this.currentAudio === audio) {
        this.releaseAudioElement(audio)
        this.currentAudio = null
        this.currentAudioId = null
        this.currentResponseId = null
      }
      this.updateState('error')
      const listeners = [...this.activeListeners]
      this.activeListeners = []
      listeners.forEach((l) => l.onError?.(err))
      setTimeout(() => {
        if (this.currentSequence === seq && this.status === 'error') {
          this.updateState('idle')
        }
      }, 50)
      return false
    }
  }

  /**
   * Enqueue a chunk when multiple chunks are being streamed.
   */
  public enqueue(options: PlaybackOptions): void {
    if (this.status === 'playing') {
      this.audioQueue.push(options)
      this.updateState('playing')
    } else {
      this.play(options)
    }
  }

  private releaseAudioElement(audio: HTMLAudioElement | null) {
    if (!audio) return
    try {
      audio.onplay = null
      audio.onended = null
      audio.onerror = null
      audio.pause()
      audio.currentTime = 0
      audio.src = ''
    } catch {}
    if (typeof window !== 'undefined' && (window as any).__helix_current_audio === audio) {
      ;(window as any).__helix_current_audio = null
    }
  }

  private stopInternal(reason: 'interrupt' | 'cancel' | 'user' = 'user', resetSequence = true): void {
    if (resetSequence) {
      this.currentSequence++
    }

    if (this.currentAudio) {
      this.releaseAudioElement(this.currentAudio)
      this.currentAudio = null
    }

    this.currentAudioId = null
    this.currentResponseId = null
    this.audioQueue = []

    if (typeof window !== 'undefined') {
      ;(window as any).__helix_current_audio = null
      if ('speechSynthesis' in window) {
        try {
          window.speechSynthesis.cancel()
        } catch {}
      }
    }

    const listeners = [...this.activeListeners]
    this.activeListeners = []

    if (this.status !== 'idle') {
      // Interruption transition: playing → stopped → idle
      this.updateState('stopped')
      listeners.forEach((l) => {
        try {
          l.onStop?.()
        } catch {}
      })
      setTimeout(() => {
        if (this.status === 'stopped') {
          this.updateState('idle')
        }
      }, 20)
    } else {
      listeners.forEach((l) => {
        try {
          l.onStop?.()
        } catch {}
      })
    }
  }

  /**
   * Play browser SpeechSynthesis utterance while synchronizing with the coordinator lifecycle.
   * Required transition: idle → playing → idle.
   */
  public speakUtterance(
    text: string,
    options?: {
      locale?: string
      speed?: number
      pitch?: number
      onStart?: () => void
      onEnd?: () => void
      onError?: (err: any) => void
      onStop?: () => void
    }
  ): boolean {
    if (typeof window === 'undefined' || !('speechSynthesis' in window) || !text) {
      return false
    }

    this.stopInternal('interrupt', true)
    const seq = ++this.currentSequence

    try {
      const utterance = new SpeechSynthesisUtterance(text)
      if (options?.locale) utterance.lang = options.locale
      if (options?.speed) utterance.rate = options.speed
      if (options?.pitch) utterance.pitch = options.pitch

      const voices = window.speechSynthesis.getVoices()
      if (voices.length > 0 && options?.locale) {
        const langPrefix = options.locale.slice(0, 2).toLowerCase()
        const match = voices.find((v) => v.lang.toLowerCase().startsWith(langPrefix))
        if (match) utterance.voice = match
      }

      utterance.onstart = () => {
        if (this.currentSequence !== seq) return
        this.updateState('playing')
        options?.onStart?.()
      }

      utterance.onend = () => {
        if (this.currentSequence !== seq) return
        this.updateState('idle')
        options?.onEnd?.()
      }

      utterance.onerror = (err) => {
        if (this.currentSequence !== seq) return
        this.updateState('error')
        options?.onError?.(err)
        setTimeout(() => {
          if (this.currentSequence === seq && this.status === 'error') {
            this.updateState('idle')
          }
        }, 50)
      }

      this.updateState('playing')
      window.speechSynthesis.speak(utterance)
      return true
    } catch (err) {
      this.updateState('idle')
      options?.onError?.(err)
      return false
    }
  }

  /**
   * Stop active audio playback and cancel any browser SpeechSynthesis.
   * Required transition: playing → stopped → idle.
   */
  public stop(reason: 'interrupt' | 'cancel' | 'user' = 'user'): void {
    this.stopInternal(reason, true)
  }

  /**
   * Clears deduplication history and resets state (useful during testing or reset).
   */
  public resetHistory(): void {
    this.stop('cancel')
    this.playedAudioIds.clear()
    this.audioQueue = []
    this.isGenerating = false
    this.updateState('idle')
  }
}

export const playbackCoordinator = new PlaybackCoordinator()

/**
 * Reactive React hook subscribing to the authoritative PlaybackCoordinator state.
 * Uses useSyncExternalStore for zero-tear, zero-latency UI synchronization.
 */
export function usePlaybackState(): PlaybackState {
  return useSyncExternalStore(
    (callback) => playbackCoordinator.subscribe(callback),
    () => playbackCoordinator.getState(),
    () => playbackCoordinator.getState()
  )
}
