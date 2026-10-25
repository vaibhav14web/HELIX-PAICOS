'use client'

import { create } from 'zustand'

export type NavSection =
  | 'dashboard'
  | 'chat'
  | 'voice'
  | 'memory'
  | 'files'
  | 'browser'
  | 'agents'
  | 'automation'
  | 'logs'
  | 'insights'
  | 'system'
  | 'goals'
  | 'settings'

interface OSState {
  activeSection: NavSection
  sidebarCollapsed: boolean
  commandPaletteOpen: boolean
  activeSessionId: string
  setActiveSessionId: (id: string) => void
  setActiveSection: (section: NavSection) => void
  toggleSidebar: () => void
  setSidebarCollapsed: (v: boolean) => void
  openCommandPalette: () => void
  closeCommandPalette: () => void
}

export const useOSStore = create<OSState>((set) => ({
  activeSection: 'dashboard',
  sidebarCollapsed: false,
  commandPaletteOpen: false,
  activeSessionId: 'default',
  setActiveSessionId: (id) => set({ activeSessionId: id }),
  setActiveSection: (section) => set({ activeSection: section }),
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
  openCommandPalette: () => set({ commandPaletteOpen: true }),
  closeCommandPalette: () => set({ commandPaletteOpen: false }),
}))
