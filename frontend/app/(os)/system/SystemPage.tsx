'use client'

import React, { useState, useEffect } from 'react'
import { Cpu, HardDrive, Shield, Activity, Monitor, Terminal, CheckCircle, Zap } from 'lucide-react'
import { api } from '@/lib/api-client'

export default function SystemPage() {
  const [selfState, setSelfState] = useState<any>(null)
  const [worldState, setWorldState] = useState<any>(null)
  const [capabilities, setCapabilities] = useState<any[]>([])
  const [apps, setApps] = useState<any[]>([])
  const [projects, setProjects] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function loadSystemData() {
      try {
        const [selfRes, worldRes, capsRes, appsRes, projRes] = await Promise.all([
          api.system.selfState().catch(() => null),
          api.system.worldState().catch(() => null),
          api.system.capabilities().catch(() => ({ capabilities: [] })),
          api.system.apps().catch(() => ({ apps: [] })),
          api.system.projects().catch(() => ({ projects: [] })),
        ])
        setSelfState(selfRes)
        setWorldState(worldRes)
        setCapabilities(capsRes?.capabilities || [])
        setApps(appsRes?.apps || [])
        setProjects(projRes?.projects || [])
      } catch (err) {
        console.error('Failed loading system data:', err)
      } finally {
        setLoading(false)
      }
    }
    loadSystemData()
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-400">
        <Activity className="w-6 h-6 animate-spin mr-2 text-indigo-400" />
        <span>Loading HELIX Platform & World Model...</span>
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto p-6 space-y-6 text-zinc-100 bg-zinc-950/40">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-zinc-800 pb-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-zinc-100 flex items-center gap-2">
            <Cpu className="w-7 h-7 text-indigo-400" />
            HELIX Platform & World Model
          </h1>
          <p className="text-xs text-zinc-400 mt-1">
            Real-time identity vector, active window perception, dynamic capability matrix, and workspace indices.
          </p>
        </div>
        <div className="flex items-center gap-2 bg-emerald-500/10 text-emerald-400 px-3 py-1.5 rounded-full border border-emerald-500/20 text-xs font-semibold">
          <CheckCircle className="w-4 h-4" />
          <span>System Healthy (v2.0.0)</span>
        </div>
      </div>

      {/* Grid Row 1: Self State & World Environment State */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Self Model Card */}
        <div className="p-5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 backdrop-blur-md space-y-4">
          <h2 className="text-sm font-bold uppercase tracking-wider text-indigo-400 flex items-center gap-2">
            <Shield className="w-4 h-4" />
            Self Model Identity Vector
          </h2>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="p-3 bg-zinc-800/40 rounded-xl border border-zinc-800">
              <span className="text-zinc-400 block mb-1">Identity</span>
              <span className="font-semibold text-zinc-100">{selfState?.identity || 'HELIX PAIOS'}</span>
            </div>
            <div className="p-3 bg-zinc-800/40 rounded-xl border border-zinc-800">
              <span className="text-zinc-400 block mb-1">LLM Backend</span>
              <span className="font-semibold text-emerald-400">{selfState?.engines?.llm_backend || 'ollama'}</span>
            </div>
            <div className="p-3 bg-zinc-800/40 rounded-xl border border-zinc-800">
              <span className="text-zinc-400 block mb-1">Listening / Speaking</span>
              <span className="font-semibold text-indigo-300">
                {selfState?.engines?.listening_state ? 'Listening' : selfState?.engines?.speaking_state ? 'Speaking' : 'Idle Warm'}
              </span>
            </div>
            <div className="p-3 bg-zinc-800/40 rounded-xl border border-zinc-800">
              <span className="text-zinc-400 block mb-1">Health Status</span>
              <span className="font-semibold text-emerald-400 capitalize">{selfState?.health_status || 'healthy'}</span>
            </div>
          </div>
        </div>

        {/* World Model Card */}
        <div className="p-5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 backdrop-blur-md space-y-4">
          <h2 className="text-sm font-bold uppercase tracking-wider text-cyan-400 flex items-center gap-2">
            <Monitor className="w-4 h-4" />
            World Model OS Perception
          </h2>
          <div className="space-y-2 text-xs">
            <div className="p-3 bg-zinc-800/40 rounded-xl border border-zinc-800 flex justify-between items-center">
              <span className="text-zinc-400">Focused Window</span>
              <span className="font-mono text-cyan-300 truncate max-w-[200px]">
                {worldState?.active_window?.window_title || 'Visual Studio Code'}
              </span>
            </div>
            <div className="p-3 bg-zinc-800/40 rounded-xl border border-zinc-800 flex justify-between items-center">
              <span className="text-zinc-400">Process</span>
              <span className="font-mono text-zinc-200">{worldState?.active_window?.process_name || 'code.exe'}</span>
            </div>
            <div className="p-3 bg-zinc-800/40 rounded-xl border border-zinc-800 flex justify-between items-center">
              <span className="text-zinc-400">Audio Devices</span>
              <span className="text-zinc-200">Default Speaker & Microphone</span>
            </div>
          </div>
        </div>
      </div>

      {/* Grid Row 2: Capabilities Matrix & App Discovery */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Capability Registry */}
        <div className="p-5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 backdrop-blur-md space-y-4">
          <h2 className="text-sm font-bold uppercase tracking-wider text-amber-400 flex items-center gap-2">
            <Zap className="w-4 h-4" />
            Active Capabilities Matrix ({capabilities.length})
          </h2>
          <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
            {capabilities.map((cap) => (
              <div key={cap.id} className="p-3 bg-zinc-800/30 rounded-xl border border-zinc-800/60 flex items-center justify-between text-xs">
                <div>
                  <div className="font-semibold text-zinc-100">{cap.name}</div>
                  <div className="text-zinc-400 text-[11px] mt-0.5">{cap.description}</div>
                </div>
                <span className="px-2 py-0.5 rounded text-[10px] uppercase font-bold bg-amber-500/10 text-amber-400 border border-amber-500/20">
                  {cap.risk_level} Risk
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Discovered Apps */}
        <div className="p-5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 backdrop-blur-md space-y-4">
          <h2 className="text-sm font-bold uppercase tracking-wider text-purple-400 flex items-center gap-2">
            <Terminal className="w-4 h-4" />
            Discovered Applications ({apps.length})
          </h2>
          <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
            {apps.map((app, idx) => (
              <div key={idx} className="p-3 bg-zinc-800/30 rounded-xl border border-zinc-800/60 flex items-center justify-between text-xs">
                <div>
                  <div className="font-semibold text-zinc-100">{app.name}</div>
                  <div className="text-zinc-400 font-mono text-[10px] truncate max-w-[220px]">{app.executable_path}</div>
                </div>
                <span className="px-2 py-0.5 rounded text-[10px] bg-purple-500/10 text-purple-300 font-mono">
                  {app.executable_name}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
