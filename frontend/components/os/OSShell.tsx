'use client'

import { useEffect } from 'react'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { CommandPalette } from './CommandPalette'
import { PermissionModal } from './PermissionModal'
import { useOSStore, type NavSection } from '@/lib/os-store'
import { getAuthToken, api } from '@/lib/api-client'
import { playbackCoordinator } from '@/lib/voice/playback-coordinator'

// Page imports
import { DashboardPage } from '@/app/(os)/dashboard/DashboardPage'
import { ChatPage } from '@/app/(os)/chat/ChatPage'
import { VoicePage } from '@/app/(os)/voice/VoicePage'
import { MemoryPage } from '@/app/(os)/memory/MemoryPage'
import { FilesPage } from '@/app/(os)/files/FilesPage'
import { BrowserPage } from '@/app/(os)/browser/BrowserPage'
import { AgentsPage } from '@/app/(os)/agents/AgentsPage'
import { AutomationPage } from '@/app/(os)/automation/AutomationPage'
import { LogsPage } from '@/app/(os)/logs/LogsPage'
import { InsightsPage } from '@/app/(os)/insights/InsightsPage'
import SystemPage from '@/app/(os)/system/SystemPage'
import GoalsPage from '@/app/(os)/goals/GoalsPage'
import { SettingsPage } from '@/app/(os)/settings/SettingsPage'

const pages: Record<NavSection, React.ComponentType> = {
  dashboard: DashboardPage,
  chat: ChatPage,
  voice: VoicePage,
  memory: MemoryPage,
  files: FilesPage,
  browser: BrowserPage,
  agents: AgentsPage,
  automation: AutomationPage,
  logs: LogsPage,
  insights: InsightsPage,
  system: SystemPage,
  goals: GoalsPage,
  settings: SettingsPage,
}

export function OSShell({ initialSection }: { initialSection?: NavSection }) {
  const { activeSection, setActiveSection, activeSessionId } = useOSStore()

  useEffect(() => {
    const sessionId = activeSessionId || 'default'
    api.session.start(sessionId).catch((err) => {
      console.warn('Failed to start session lifecycle:', err)
    })

    const handleBeforeUnload = () => {
      api.session.end(sessionId).catch(() => {})
    }
    window.addEventListener('beforeunload', handleBeforeUnload)

    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload)
    }
  }, [activeSessionId])

  useEffect(() => {
    if (initialSection) {
      setActiveSection(initialSection)
    } else if (typeof window !== 'undefined') {
      const path = window.location.pathname.replace(/^\//, '').toLowerCase()
      if (path && path in pages) {
        setActiveSection(path as NavSection)
      }
    }
  }, [initialSection, setActiveSection])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const targetPath = activeSection === 'dashboard' ? '/' : `/${activeSection}`
    if (window.location.pathname !== targetPath) {
      window.history.pushState(null, '', targetPath)
    }

    const onPopState = () => {
      const path = window.location.pathname.replace(/^\//, '').toLowerCase()
      const resolved = (path in pages ? path : 'dashboard') as NavSection
      setActiveSection(resolved)
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [activeSection, setActiveSection])

  const ActivePage = pages[activeSection] || pages.dashboard

  useEffect(() => {
    const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
    const wsUrl = API_BASE.replace(/^http/, 'ws') + '/ws/events'

    let ws: WebSocket | null = null
    let reconnectTimeout: NodeJS.Timeout

    function connect() {
      const token = getAuthToken()
      const fullWsUrl = wsUrl + (token ? `?token=${encodeURIComponent(token)}` : '')

      ws = new WebSocket(fullWsUrl)

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)

          // Dispatch real-time WebSocket event for views like ChatPage
          if (typeof window !== 'undefined') {
            window.dispatchEvent(new CustomEvent('helix:ws_message', { detail: data }))
          }

          if ((data.type === 'chat_audio' || data.type === 'response') && data.audio) {
            playbackCoordinator.play({
              audioId: data.audio_id,
              audioBase64: data.audio,
              source: 'ws',
            })
          }
        } catch (err) {
          console.warn('Error handling websocket message:', err)
        }
      }

      ws.onclose = () => {
        reconnectTimeout = setTimeout(connect, 3000)
      }

      ws.onerror = () => {
        // WebSocket error event object is empty in browser specs; close quietly to trigger reconnection
        try {
          ws?.close()
        } catch {
          // ignore
        }
      }
    }

    connect()

    return () => {
      if (ws) {
        ws.onclose = null
        ws.close()
      }
      clearTimeout(reconnectTimeout)
    }
  }, [])

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background dot-grid">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <Topbar />
        <main className="flex-1 overflow-auto">
          <ActivePage />
        </main>
      </div>
      <CommandPalette />
      <PermissionModal />
    </div>
  )
}
