'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { GlassCard } from '@/components/os/GlassCard'
import { useChat } from '@/lib/hooks/use-chat'
import { useOSStore } from '@/lib/os-store'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, generateRequestId } from '@/lib/api-client'
import { Send, Plus, Paperclip, Mic, Sparkles, Copy, ThumbsUp, RotateCcw, ChevronDown, ExternalLink, Square } from 'lucide-react'
import { cn } from '@/lib/utils'

import { useVoice } from '@/lib/hooks/use-voice'
import { cleanTextForSpeech } from '@/lib/speech-cleaner'
import { playbackCoordinator } from '@/lib/voice/playback-coordinator'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
  thinking?: boolean
}

const suggestions = [
  'Summarize my memory graph',
  'Deploy a research agent',
  'Show me today\'s analytics',
  'What automations are scheduled?',
]

function TypingIndicator() {
  return (
    <div className="flex items-center gap-1.5 px-1">
      {[0, 1, 2].map((i) => (
        <motion.div
          key={i}
          className="w-1.5 h-1.5 rounded-full bg-[#00d4ff]"
          animate={{ opacity: [0.3, 1, 0.3] }}
          transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.2 }}
        />
      ))}
    </div>
  )
}

function MessageBubble({ msg }: { msg: Message }) {
  const isAssistant = msg.role === 'assistant'
  const lines = msg.content.split('\n')

  const urlMatch = msg.content.match(/https?:\/\/[^\s\)\"\'\>]+/)
  const detectedUrl = urlMatch ? urlMatch[0] : null

  const renderContent = (text: string) => {
    return text
      .replace(
        /\[(.*?)\]\((https?:\/\/[^\s\)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer" class="text-[#00d4ff] underline hover:text-cyan-300 font-medium inline-flex items-center gap-1">$1 ↗</a>'
      )
      .replace(/\*\*(.*?)\*\*/g, '<strong class="text-zinc-100">$1</strong>')
      .replace(/`(.*?)`/g, '<code class="font-mono text-[#00d4ff] bg-[rgba(0,212,255,0.08)] px-1 py-0.5 rounded text-xs">$1</code>')
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className={cn('flex gap-3', isAssistant ? 'justify-start' : 'justify-end')}
    >
      {isAssistant && (
        <div className="w-7 h-7 rounded-lg bg-[rgba(0,212,255,0.1)] border border-[rgba(0,212,255,0.2)] flex items-center justify-center shrink-0 mt-0.5">
          <Sparkles className="w-3.5 h-3.5 text-[#00d4ff]" />
        </div>
      )}
      <div className={cn('max-w-[78%] space-y-1', !isAssistant && 'items-end flex flex-col')}>
        <div
          className={cn(
            'rounded-2xl px-4 py-3 text-sm leading-relaxed',
            isAssistant
              ? 'glass text-zinc-300 rounded-tl-sm'
              : 'bg-[rgba(0,212,255,0.1)] border border-[rgba(0,212,255,0.2)] text-zinc-200 rounded-tr-sm',
          )}
        >
          {msg.thinking ? (
            <TypingIndicator />
          ) : (
            <div className="space-y-1.5">
              {lines.map((line, i) => (
                <p
                  key={i}
                  dangerouslySetInnerHTML={{ __html: renderContent(line) || '&nbsp;' }}
                  className={line === '' ? 'h-2' : ''}
                />
              ))}
              {detectedUrl && isAssistant && !msg.thinking && (
                <div className="pt-2 border-t border-[rgba(255,255,255,0.08)] mt-2">
                  <a
                    href={detectedUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[rgba(0,212,255,0.12)] hover:bg-[rgba(0,212,255,0.22)] border border-[rgba(0,212,255,0.35)] text-[#00d4ff] hover:text-cyan-200 text-xs font-medium transition-all shadow-sm group/btn"
                  >
                    <ExternalLink className="w-3.5 h-3.5 group-hover/btn:translate-x-0.5 group-hover/btn:-translate-y-0.5 transition-transform" />
                    <span>Open in Browser</span>
                  </a>
                </div>
              )}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2 px-1">
          <span className="text-[10px] text-zinc-700 font-mono">
            {msg.timestamp.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })}
          </span>
          {isAssistant && !msg.thinking && (
            <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100">
              {[Copy, ThumbsUp, RotateCcw].map((Icon, i) => (
                <button key={i} className="p-0.5 text-zinc-700 hover:text-zinc-400 transition-colors">
                  <Icon className="w-3 h-3" />
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </motion.div>
  )
}

function deduplicateMessages(msgs: Message[]): Message[] {
  const result: Message[] = []
  for (const message of msgs) {
    if (message.thinking) {
      result.push(message)
      continue
    }

    // Voice results can arrive both in the HTTP response and the WebSocket event.
    // Collapse only a short-lived matching echo so distinct repeated messages remain visible.
    const duplicate = result.some((existing) =>
      !existing.thinking &&
      existing.role === message.role &&
      existing.content.trim() === message.content.trim() &&
      Math.abs(existing.timestamp.getTime() - message.timestamp.getTime()) <= 10_000
    )
    if (!duplicate) result.push(message)
  }
  return result
}

export function ChatPage() {
  const queryClient = useQueryClient()
  const { activeSessionId, setActiveSessionId } = useOSStore()
  const { history, stopGeneration } = useChat(activeSessionId)
  const {
    command: voiceCommand,
    profiles: voiceProfilesQuery,
    selectProfile: selectVoiceProfile,
    enqueueAudio,
    stopAudio,
    isAudioPlaying,
  } = useVoice(activeSessionId)
  const activeVoiceProfileId = voiceProfilesQuery.data?.active_profile || voiceProfilesQuery.data?.active_profile_id || 'aria'
  const sessions = useQuery({
    queryKey: ['conversation_sessions'],
    queryFn: () => api.chat.sessions(),
    refetchInterval: 5_000,
  })

  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [uploadedFiles, setUploadedFiles] = useState<Array<{ id: string; name: string }>>([])
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [isTyping, setIsTyping] = useState(false)
  const [isRecording, setIsRecording] = useState(false)
  const [showSuggestions, setShowSuggestions] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const audioContextRef = useRef<AudioContext | null>(null)
  const animFrameRef = useRef<number | null>(null)
  const maxTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  // Correlation & lifecycle tracking refs
  const activeRequestIdRef = useRef<string | null>(null)
  const activeGenIdRef = useRef<string | null>(null)

  const handleStop = useCallback(async () => {
    const currentReqId = activeRequestIdRef.current
    stopAudio(currentReqId || undefined)
    playbackCoordinator.stop()
    setIsTyping(false)
    activeRequestIdRef.current = null
    activeGenIdRef.current = null

    setMessages((prev) =>
      prev.map((m) => {
        if (m.thinking) {
          return {
            ...m,
            thinking: false,
            content: m.content || '[Generation stopped]',
          }
        }
        return m
      })
    )

    try {
      await api.chat.stop(activeSessionId, currentReqId || undefined)
    } catch (e) {
      console.warn('[ChatPage] Stop request failed:', e)
    }
  }, [activeSessionId, stopAudio])

  const handleAttachmentSelection = useCallback(async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    if (!files.length) return
    setUploadError(null)
    setIsUploading(true)
    try {
      for (const file of files) {
        const form = new FormData()
        form.append('file', file)
        const result = await api.files.upload(form)
        setUploadedFiles((current) => [...current, { id: `${Date.now()}-${file.name}`, name: result.name }])
      }
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : 'File upload failed.')
    } finally {
      setIsUploading(false)
      event.target.value = ''
    }
  }, [])
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
      stopAudio()
      playbackCoordinator.stop()
    }
  }, [stopAudioCapture, stopAudio])

  const toggleMic = useCallback(async () => {
    if (isRecording) {
      stopAudioCapture()
      if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
        mediaRecorderRef.current.stop()
      }
      setIsRecording(false)
      return
    }

    playbackCoordinator.stop()
    stopAudioCapture()
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' })
      mediaRecorderRef.current = recorder
      audioChunksRef.current = []

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data)
      }

      recorder.onstop = async () => {
        stopAudioCapture()
        setIsRecording(false)
        stream.getTracks().forEach((t) => t.stop())
        const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' })
        if (blob.size === 0) return

        setIsTyping(true)
        try {
          const result = await voiceCommand.mutateAsync({ file: blob, sessionId: activeSessionId })
queryClient.invalidateQueries({ queryKey: ['conversation', activeSessionId] })
          queryClient.invalidateQueries({ queryKey: ['conversation_sessions'] })
          const playVoiceFallback = (resText: string) => {
            const cleaned = cleanTextForSpeech(resText)
            if (typeof window !== 'undefined' && 'speechSynthesis' in window && cleaned) {
              try {
                window.speechSynthesis.cancel()
                window.speechSynthesis.speak(new SpeechSynthesisUtterance(cleaned))
              } catch (e) {
                console.warn('[ChatPage] Browser speech synthesis fallback failed:', e)
              }
            }
          }
          if (result.audio) {
            playbackCoordinator.play({
              audioId: result.audio_id || undefined,
              audioBase64: result.audio,
              source: 'http',
              onError: () => {
                if (result.response) playVoiceFallback(result.response)
              },
            })
          } else if (result.response) {
            playVoiceFallback(result.response)
          }
        } catch {
          setMessages((prev) => [
            ...prev,
            { id: Date.now().toString(), role: 'assistant', content: 'Voice processing failed. Please try again.', timestamp: new Date() },
          ])
        } finally {
          setIsTyping(false)
        }
      }

      recorder.start()
      setIsRecording(true)

      // VAD: Speech Activity & Silence auto-finish
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
          if (!mediaRecorderRef.current || mediaRecorderRef.current.state !== 'recording') return
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
                // Natural pause of 2.2s after speaking -> finish recording
                stopAudioCapture()
                if (mediaRecorderRef.current?.state === 'recording') {
                  mediaRecorderRef.current.stop()
                }
                return
              }
            } else if (now - listenStartTime > 12000) {
              stopAudioCapture()
              if (mediaRecorderRef.current?.state === 'recording') {
                mediaRecorderRef.current.stop()
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
        if (mediaRecorderRef.current?.state === 'recording') {
          mediaRecorderRef.current.stop()
        }
      }, 35000)
    } catch {
      setIsRecording(false)
    }
  }, [isRecording, voiceCommand, activeSessionId, stopAudioCapture])

  useEffect(() => {
    const entries = history.data?.entries || []
    if (entries.length === 0) return
    setMessages((prev) => {
      const thinkingMsg = prev.find((m) => m.thinking)
      const serverMsgs: Message[] = entries.map((e) => ({
        id: e.timestamp ?? Math.random().toString(),
        role: e.role as 'user' | 'assistant',
        content: e.content,
        timestamp: new Date(e.timestamp ?? Date.now()),
      }))
      const combined = [...serverMsgs]
      const lastServer = serverMsgs[serverMsgs.length - 1]
      if (lastServer) {
        const lastServerTime = lastServer.timestamp.getTime()
        for (const p of prev) {
          if (p.thinking) continue
          if (p.timestamp.getTime() > lastServerTime + 1000) {
            if (!serverMsgs.some((s) =>
              s.role === p.role &&
              s.content.trim() === p.content.trim() &&
              Math.abs(s.timestamp.getTime() - p.timestamp.getTime()) <= 10_000
            )) {
              combined.push(p)
            }
          }
        }
      }
      if (thinkingMsg) {
        combined.push(thinkingMsg)
      }
      return deduplicateMessages(combined)
    })
  }, [history.data])

  // Synchronize incoming real-time voice, text tokens, and audio WebSocket broadcasts into the chat conversation
  useEffect(() => {
    const handleWsMessage = (e: Event) => {
      const data = (e as CustomEvent).detail
      if (!data) return
      if (data.session_id && data.session_id !== activeSessionId) return

      // Correlation validation:
      // If incoming event carries request_id, reject if mismatched
      if (data.request_id && activeRequestIdRef.current && data.request_id !== activeRequestIdRef.current) {
        return
      }

      // If incoming event carries correlation_id (format: req_<uuid>::gen::<uuid>)
      if (data.correlation_id) {
        if (activeGenIdRef.current && data.correlation_id !== activeGenIdRef.current) {
          return
        }
        if (!activeGenIdRef.current && activeRequestIdRef.current) {
          activeGenIdRef.current = data.correlation_id
        }
      }

      if (data.type === 'transcription' && data.text) {
        setMessages((prev) => {
          const last = prev[prev.length - 1]
          if (last && last.role === 'user' && last.content.trim() === data.text.trim()) {
            return prev
          }
          const userMsg: Message = {
            id: Date.now().toString(),
            role: 'user',
            content: data.text,
            timestamp: new Date(),
          }
          const thinkingMsg: Message = {
            id: (Date.now() + 1).toString(),
            role: 'assistant',
            content: '',
            timestamp: new Date(),
            thinking: true,
          }
          return deduplicateMessages([...prev, userMsg, thinkingMsg])
        })
      } else if (data.type === 'llm_chunk' && data.text) {
        // Stream token chunks progressively into the active assistant bubble
        setMessages((prev) => {
          const idx = prev.findIndex((m) => m.thinking || m.id === activeRequestIdRef.current)
          if (idx !== -1) {
            const current = prev[idx]
            const updated: Message = {
              ...current,
              content: current.content + data.text,
              thinking: false,
            }
            const copy = [...prev]
            copy[idx] = updated
            return copy
          }
          const newMsg: Message = {
            id: activeRequestIdRef.current || Date.now().toString(),
            role: 'assistant',
            content: data.text,
            timestamp: new Date(),
          }
          return deduplicateMessages([...prev, newMsg])
        })
      } else if ((data.type === 'response' || data.type === 'chat_audio') && (data.text || data.audio)) {
        setIsTyping(false)
        if (data.text) {
          setMessages((prev) => {
            const withoutThinking = prev.filter((m) => !m.thinking && m.id !== activeRequestIdRef.current)
            const assistantMsg: Message = {
              id: activeRequestIdRef.current || (Date.now() + 1).toString(),
              role: 'assistant',
              content: data.text,
              timestamp: new Date(),
            }
            return deduplicateMessages([...withoutThinking, assistantMsg])
          })
          queryClient.invalidateQueries({ queryKey: ['conversation', activeSessionId] })
          queryClient.invalidateQueries({ queryKey: ['conversation_sessions'] })
        }

        // Sequential audio FIFO queue dispatch
        if (data.audio) {
          enqueueAudio({
            id: data.audio_id || `aud_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
            audioBase64: data.audio,
            requestId: data.request_id || activeRequestIdRef.current || undefined,
            correlationId: data.correlation_id || activeGenIdRef.current || undefined,
            text: data.text,
          })
        }
      }
    }

    window.addEventListener('helix:ws_message', handleWsMessage)
    return () => window.removeEventListener('helix:ws_message', handleWsMessage)
  }, [activeSessionId, queryClient, enqueueAudio])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMsg = useCallback(async (text: string = input) => {
    if (!text.trim()) return
    const reqId = generateRequestId()
    activeRequestIdRef.current = reqId
    activeGenIdRef.current = null

    const userMsg: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: text,
      timestamp: new Date(),
    }
    const thinkingMsg: Message = {
      id: reqId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      thinking: true,
    }
    setMessages((prev) => [...prev, userMsg, thinkingMsg])
    setInput('')
    setIsTyping(true)
    setShowSuggestions(false)

    try {
      const result = await api.chat.send(text, activeSessionId, reqId)
      // Check if this request is still active (not cancelled or superseded)
      if (activeRequestIdRef.current !== reqId) {
        return
      }

      setMessages((prev) => {
        const withoutThinking = prev.filter((m) => !m.thinking && m.id !== reqId)
        return deduplicateMessages([
          ...withoutThinking,
          { id: reqId, role: 'assistant', content: result.response, timestamp: new Date() },
        ])
      })

      const audioData = (result as any).audio
      const audioId = (result as any).audio_id
      if (audioData) {
        enqueueAudio({
          id: audioId || `aud_${Date.now()}`,
          audioBase64: audioData,
          requestId: reqId,
          text: result.response,
          onError: () => {
            if (result.response && typeof window !== 'undefined' && 'speechSynthesis' in window) {
              const cleaned = cleanTextForSpeech(result.response)
              if (cleaned) {
                try {
                  window.speechSynthesis.cancel()
                  window.speechSynthesis.speak(new SpeechSynthesisUtterance(cleaned))
                } catch (e) {
                  console.warn('[ChatPage] Browser speech synthesis fallback failed:', e)
                }
              }
            }
          },
        })
      }
    } catch (err: any) {
      if (activeRequestIdRef.current === reqId) {
        setMessages((prev) => [
          ...prev.filter((m) => !m.thinking && m.id !== reqId),
          { id: Date.now().toString(), role: 'assistant', content: 'Sorry, I encountered an error processing your request.', timestamp: new Date() },
        ])
      }
    } finally {
      if (activeRequestIdRef.current === reqId) {
        setIsTyping(false)
      }
    }
  }, [input, activeSessionId, enqueueAudio])

  return (
    <div className="flex h-full">
      {/* Conversation list */}
      <div className="w-60 shrink-0 border-r border-[rgba(255,255,255,0.06)] flex flex-col bg-[#0f0f11]">
        <div className="p-3 border-b border-[rgba(255,255,255,0.06)]">
          <button
            onClick={() => {
              const newId = `chat_${Date.now()}`
              setActiveSessionId(newId)
              setMessages([])
            }}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-lg bg-[rgba(0,212,255,0.08)] border border-[rgba(0,212,255,0.15)] text-[#00d4ff] text-sm font-medium hover:bg-[rgba(0,212,255,0.12)] transition-colors"
          >
            <Plus className="w-4 h-4" />
            New Chat
          </button>
        </div>
        <div className="flex-1 overflow-y-auto py-2">
          {sessions.data?.sessions && sessions.data.sessions.length > 0 ? (
            sessions.data.sessions.map((sid, i) => {
              const isActive = sid === activeSessionId
              let displayName = sid
              if (sid === 'default') {
                displayName = 'Default Chat'
              } else if (sid.startsWith('chat_')) {
                const ts = parseInt(sid.replace('chat_', ''), 10)
                if (!isNaN(ts)) {
                  displayName = `Chat - ${new Date(ts).toLocaleString(undefined, {
                    month: 'short',
                    day: 'numeric',
                    hour: 'numeric',
                    minute: '2-digit',
                    hour12: false,
                  })}`
                }
              }

              return (
                <button
                  key={sid}
                  onClick={() => setActiveSessionId(sid)}
                  className={cn(
                    "w-full text-left px-4 py-3 border-b border-[rgba(255,255,255,0.03)] hover:bg-[rgba(255,255,255,0.03)] transition-colors flex flex-col gap-1",
                    isActive && "bg-[rgba(0,212,255,0.06)] border-r-2 border-[#00d4ff]"
                  )}
                >
                  <span className={cn("text-xs font-semibold", isActive ? "text-[#00d4ff]" : "text-zinc-300")}>
                    {displayName}
                  </span>
                  <span className="text-[9px] text-zinc-600 font-mono">{sid}</span>
                </button>
              )
            })
          ) : (
            <div className="px-4 py-6 text-center">
              <p className="text-xs text-zinc-700">No previous conversations</p>
            </div>
          )}
        </div>
      </div>

      {/* Chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top Header Bar with Voice Personality Switcher */}
        <div className="h-11 border-b border-[rgba(255,255,255,0.06)] px-6 flex items-center justify-between shrink-0 bg-[#0f0f11]/50 backdrop-blur-sm">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-zinc-300 font-mono">
              {activeSessionId === 'default' ? 'Default Conversation' : activeSessionId}
            </span>
            <span className="w-1.5 h-1.5 rounded-full bg-[#22c55e]" />
          </div>

          <div className="flex items-center gap-1.5 bg-[rgba(255,255,255,0.03)] border border-[rgba(255,255,255,0.07)] rounded-full px-2 py-0.5">
            <Mic className="w-3 h-3 text-[#00d4ff]" />
            <span className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider pr-1">Voice:</span>
            {[
              { id: 'aria', label: 'ARIA' },
              { id: 'nova', label: 'NOVA' },
              { id: 'orion', label: 'ORION' },
            ].map((v) => {
              const isActive = activeVoiceProfileId === v.id
              return (
                <button
                  key={v.id}
                  onClick={() => selectVoiceProfile.mutate({ profileId: v.id, sessionId: activeSessionId })}
                  className={cn(
                    'px-2 py-0.5 rounded-full text-[10px] font-mono font-medium transition-all',
                    isActive
                      ? 'bg-[rgba(0,212,255,0.2)] text-[#00d4ff] border border-[rgba(0,212,255,0.4)] shadow-[0_0_8px_rgba(0,212,255,0.2)]'
                      : 'text-zinc-500 hover:text-zinc-300'
                  )}
                >
                  {v.label}
                </button>
              )
            })}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6 space-y-5 group">
          {messages.length === 0 && !history.isLoading && (
            <div className="flex items-center justify-center h-full">
              <div className="text-center space-y-2">
                <Sparkles className="w-10 h-10 text-[#00d4ff] mx-auto opacity-50" />
                <p className="text-sm text-zinc-600">Start a conversation with HELIX</p>
              </div>
            </div>
          )}
          {deduplicateMessages(messages).map((msg) => (
            <MessageBubble key={msg.id} msg={msg} />
          ))}
          {history.isLoading && (
            <div className="flex items-center gap-2 text-xs text-zinc-600 font-mono px-2">
              <div className="w-1.5 h-1.5 rounded-full bg-[#00d4ff] animate-pulse-glow" />
              Loading history...
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Suggestions */}
        <AnimatePresence>
          {showSuggestions && (
            <motion.div
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
              className="px-6 pb-2 flex gap-2 flex-wrap"
            >
              {suggestions.map((s) => (
                <button
                  key={s}
                  onClick={() => {
                    setInput(s)
                    sendMsg(s)
                  }}
                  className="text-xs px-3 py-1.5 rounded-full glass border border-[rgba(255,255,255,0.07)] text-zinc-400 hover:text-zinc-200 hover:border-[rgba(0,212,255,0.2)] transition-all"
                >
                  {s}
                </button>
              ))}
            </motion.div>
          )}
        </AnimatePresence>

        {/* Input bar */}
        <div className="p-4 border-t border-[rgba(255,255,255,0.06)]">
          <div className="glass rounded-2xl flex items-end gap-3 px-4 py-3 border-[rgba(255,255,255,0.08)] focus-within:border-[rgba(0,212,255,0.25)] focus-within:glow-cyan-sm transition-all">
            <button
              onClick={() => setShowSuggestions((v) => !v)}
              className="text-zinc-600 hover:text-zinc-300 transition-colors shrink-0 pb-0.5"
            >
              <ChevronDown className={cn('w-4 h-4 transition-transform', showSuggestions && 'rotate-180')} />
            </button>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  sendMsg()
                }
              }}
              placeholder="Message HELIX..."
              rows={1}
              className="flex-1 bg-transparent text-sm text-zinc-200 placeholder:text-zinc-600 outline-none resize-none leading-relaxed"
              style={{ minHeight: '24px', maxHeight: '120px' }}
            />
            <div className="flex items-center gap-2 shrink-0">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={handleAttachmentSelection}
                aria-label="Choose files to upload"
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading}
                title={isUploading ? 'Uploading files…' : 'Attach files'}
                className="text-zinc-600 hover:text-zinc-400 disabled:opacity-40 transition-colors"
              >
                <Paperclip className="w-4 h-4" />
              </button>
              <button
                onClick={toggleMic}
                title={isRecording ? 'Stop Recording' : 'Record Voice Input'}
                className={cn(
                  'transition-colors p-1 rounded-lg',
                  isRecording ? 'text-red-500 bg-red-500/10 animate-pulse' : 'text-zinc-600 hover:text-zinc-400',
                )}
              >
                <Mic className="w-4 h-4" />
              </button>
              {isTyping ? (
                <button
                  type="button"
                  onClick={handleStop}
                  title="Stop generation"
                  className="w-8 h-8 rounded-xl bg-red-500/20 text-red-400 border border-red-500/30 flex items-center justify-center hover:bg-red-500/30 transition-all"
                >
                  <Square className="w-3.5 h-3.5 fill-current" />
                </button>
              ) : (
                <button
                  onClick={() => sendMsg()}
                  disabled={!input.trim()}
                  title="Send message"
                  className="w-8 h-8 rounded-xl bg-[#00d4ff] text-[#09090b] flex items-center justify-center hover:bg-[#00d4ff]/90 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                >
                  <Send className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          </div>
          {(uploadedFiles.length > 0 || uploadError || isUploading) && (
            <div className="mt-2 flex flex-wrap items-center gap-2 px-1 text-xs" aria-live="polite">
              {isUploading && <span className="text-zinc-500">Uploading files…</span>}
              {uploadedFiles.map((file) => (
                <span key={file.id} className="rounded-full border border-[rgba(0,212,255,0.18)] px-2 py-1 text-zinc-400">
                  Uploaded: {file.name}
                </span>
              ))}
              {uploadError && <span role="alert" className="text-red-400">{uploadError}</span>}
            </div>
          )}
          <div className="text-center mt-2">
            <span className="text-[10px] text-zinc-700 font-mono">HELIX can make mistakes. Verify critical decisions.</span>
          </div>
        </div>
      </div>
    </div>
  )
}
