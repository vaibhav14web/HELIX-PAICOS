import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useCallback, useSyncExternalStore } from 'react'
import { api } from '../api-client.ts'

export interface AudioQueueItem {
  id: string
  audioBase64?: string
  audioUrl?: string
  blob?: Blob
  requestId?: string
  correlationId?: string
  text?: string
  onStart?: () => void
  onEnd?: () => void
  onError?: (err: any) => void
}

export class VoiceAudioQueue {
  private queue: AudioQueueItem[] = []
  private currentAudio: HTMLAudioElement | null = null
  private currentBlobUrl: string | null = null
  private currentItem: AudioQueueItem | null = null
  private _isPlaying: boolean = false
  private currentSequence: number = 0
  private playedIds: Set<string> = new Set()
  private listeners: Set<() => void> = new Set()
  private allocatedUrls: Set<string> = new Set()

  public isPlaying(): boolean {
    return this._isPlaying
  }

  public getCurrentItem(): AudioQueueItem | null {
    return this.currentItem
  }

  public getQueueLength(): number {
    return this.queue.length
  }

  public subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private notify(): void {
    this.listeners.forEach((l) => {
      try {
        l()
      } catch {}
    })
  }

  private releaseAudio(audio: HTMLAudioElement | null, blobUrl: string | null): void {
    if (audio) {
      try {
        audio.onplay = null
        audio.onended = null
        audio.onerror = null
        audio.pause()
        audio.currentTime = 0
        audio.src = ''
      } catch {}
    }
    if (blobUrl) {
      try {
        if (typeof URL !== 'undefined' && typeof URL.revokeObjectURL === 'function') {
          URL.revokeObjectURL(blobUrl)
        }
      } catch {}
      this.allocatedUrls.delete(blobUrl)
    }
  }

  public enqueue(item: AudioQueueItem): boolean {
    if (!item || !item.id) return false
    if (!item.audioBase64 && !item.audioUrl && !item.blob) return false

    // Deduplication check
    if (this.playedIds.has(item.id)) {
      return false
    }

    // Generation replacement check:
    // If incoming item has a different requestId than currently active generation,
    // cancel and purge the prior generation to prevent stale or overlapping audio.
    if (item.requestId) {
      if (this.currentItem?.requestId && this.currentItem.requestId !== item.requestId) {
        this.stop(this.currentItem.requestId)
      } else if (this.queue.length > 0 && this.queue[0]?.requestId && this.queue[0].requestId !== item.requestId) {
        this.clearQueue()
      }
    }

    this.playedIds.add(item.id)
    if (this.playedIds.size > 200) {
      const oldest = Array.from(this.playedIds).slice(0, 50)
      oldest.forEach((id) => this.playedIds.delete(id))
    }

    this.queue.push(item)
    this.notify()

    if (!this._isPlaying) {
      this.playNext()
    }
    return true
  }

  private async playNext(): Promise<void> {
    if (this._isPlaying || this.queue.length === 0) {
      return
    }

    const item = this.queue.shift()!
    const seq = ++this.currentSequence
    this.currentItem = item
    this._isPlaying = true
    this.notify()

    let srcUrl = ''
    let createdBlobUrl: string | null = null

    try {
      if (item.audioUrl) {
        srcUrl = item.audioUrl
      } else if (item.blob) {
        if (typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') {
          createdBlobUrl = URL.createObjectURL(item.blob)
          srcUrl = createdBlobUrl
          this.allocatedUrls.add(createdBlobUrl)
        }
      } else if (item.audioBase64) {
        const raw = item.audioBase64
        const atobFn = typeof atob === 'function' ? atob : typeof window !== 'undefined' ? (window as any).atob : null
        if (raw.startsWith('data:')) {
          srcUrl = raw
        } else if (atobFn) {
          // Convert base64 to Blob URL for clean deterministic lifecycle and revoking
          try {
            const byteChars = atobFn(raw)
            const byteNumbers = new Array(byteChars.length)
            for (let i = 0; i < byteChars.length; i++) {
              byteNumbers[i] = byteChars.charCodeAt(i)
            }
            const byteArray = new Uint8Array(byteNumbers)
            const blob = new Blob([byteArray], { type: 'audio/wav' })
            if (typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') {
              createdBlobUrl = URL.createObjectURL(blob)
              srcUrl = createdBlobUrl
              this.allocatedUrls.add(createdBlobUrl)
            } else {
              srcUrl = `data:audio/wav;base64,${raw}`
            }
          } catch {
            srcUrl = `data:audio/wav;base64,${raw}`
          }
        } else {
          srcUrl = `data:audio/wav;base64,${raw}`
        }
      }

      if (!srcUrl) {
        throw new Error('No valid audio source found')
      }

      const audio = new Audio(srcUrl)
      this.currentAudio = audio
      this.currentBlobUrl = createdBlobUrl

      const handleCompletion = () => {
        if (this.currentSequence !== seq) return
        this.releaseAudio(audio, createdBlobUrl)
        this.currentAudio = null
        this.currentBlobUrl = null
        this.currentItem = null
        this._isPlaying = false
        this.notify()
        try {
          item.onEnd?.()
        } catch {}
        this.playNext()
      }

      const handleError = (err: any) => {
        if (this.currentSequence !== seq) return
        console.warn('[VoiceAudioQueue] Segment playback error:', err)
        this.releaseAudio(audio, createdBlobUrl)
        this.currentAudio = null
        this.currentBlobUrl = null
        this.currentItem = null
        this._isPlaying = false
        this.notify()
        try {
          item.onError?.(err)
        } catch {}
        this.playNext()
      }

      audio.onplay = () => {
        if (this.currentSequence !== seq) return
        try {
          item.onStart?.()
        } catch {}
      }

      audio.onended = () => {
        handleCompletion()
      }

      audio.onerror = (e) => {
        handleError(e)
      }

      await audio.play().catch((err) => {
        handleError(err)
      })
    } catch (err) {
      if (this.currentSequence === seq) {
        this.releaseAudio(this.currentAudio, createdBlobUrl)
        this.currentAudio = null
        this.currentBlobUrl = null
        this.currentItem = null
        this._isPlaying = false
        this.notify()
        try {
          item.onError?.(err)
        } catch {}
        this.playNext()
      }
    }
  }

  public stop(requestId?: string): void {
    this.currentSequence++

    // Purge queue items
    if (requestId) {
      this.queue = this.queue.filter((i) => i.requestId !== requestId)
    } else {
      this.queue = []
    }

    // Stop current audio if target matches
    if (!requestId || this.currentItem?.requestId === requestId) {
      if (this.currentAudio) {
        this.releaseAudio(this.currentAudio, this.currentBlobUrl)
        this.currentAudio = null
        this.currentBlobUrl = null
      }
      this.currentItem = null
      this._isPlaying = false
    }

    // Revoke any orphan allocated URLs if completely stopping
    if (!requestId || this.queue.length === 0) {
      if (typeof URL !== 'undefined' && typeof URL.revokeObjectURL === 'function') {
        this.allocatedUrls.forEach((u) => {
          try {
            URL.revokeObjectURL(u)
          } catch {}
        })
      }
      this.allocatedUrls.clear()
    }

    this.notify()
  }

  public clearQueue(): void {
    this.queue = []
    this.notify()
  }

  public reset(): void {
    this.stop()
    this.playedIds.clear()
  }
}

export const voiceAudioQueue = new VoiceAudioQueue()

export function useVoice(sessionId = 'default') {
  const queryClient = useQueryClient()

  const isAudioPlaying = useSyncExternalStore(
    (callback) => voiceAudioQueue.subscribe(callback),
    () => voiceAudioQueue.isPlaying(),
    () => false
  )

  const enqueueAudio = useCallback((item: AudioQueueItem) => {
    return voiceAudioQueue.enqueue(item)
  }, [])

  const stopAudio = useCallback((requestId?: string) => {
    voiceAudioQueue.stop(requestId)
  }, [])

  const clearAudioQueue = useCallback(() => {
    voiceAudioQueue.clearQueue()
  }, [])

  const profiles = useQuery({
    queryKey: ['voice_profiles', sessionId],
    queryFn: () => api.voice.getProfiles(sessionId),
  })

  const selectProfile = useMutation({
    mutationFn: ({ profileId, sessionId: sid }: { profileId: string; sessionId?: string }) =>
      api.voice.selectProfile(profileId, sid || sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['voice_profiles'] })
    },
  })

  const previewProfile = useMutation({
    mutationFn: (arg: string | { profileId: string }) => {
      const profileId = typeof arg === 'string' ? arg : arg.profileId
      return api.voice.previewProfile(profileId)
    },
  })

  const speak = useMutation({
    mutationFn: ({ text, sessionId: sid }: { text: string; sessionId?: string }) =>
      api.voice.speak(text, sid || sessionId),
  })

  const command = useMutation({
    mutationFn: ({ file, sessionId: sid }: { file: Blob; sessionId?: string }) =>
      api.voice.command(file, sid || sessionId),
    onSuccess: (_, variables) => {
      const sid = variables.sessionId || sessionId || 'default'
      queryClient.invalidateQueries({ queryKey: ['conversation', sid] })
      queryClient.invalidateQueries({ queryKey: ['conversation_sessions'] })
    },
  })

  const transcribe = useMutation({
    mutationFn: ({ file, sessionId: sid }: { file: Blob; sessionId?: string }) =>
      api.voice.transcribe(file, sid || sessionId),
  })

  return {
    speak,
    command,
    transcribe,
    profiles,
    selectProfile,
    previewProfile,
    enqueueAudio,
    stopAudio,
    clearAudioQueue,
    isAudioPlaying,
  }
}
