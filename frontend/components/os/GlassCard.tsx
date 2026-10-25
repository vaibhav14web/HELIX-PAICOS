import { cn } from '@/lib/utils'
import { type ReactNode } from 'react'

interface GlassCardProps {
  children: ReactNode
  className?: string
  glow?: boolean
  onClick?: () => void
}

export function GlassCard({ children, className, glow, onClick }: GlassCardProps) {
  return (
    <div
      onClick={onClick}
      className={cn(
        'glass rounded-xl p-4',
        glow && 'glow-cyan',
        onClick && 'cursor-pointer hover:border-[rgba(0,212,255,0.2)] transition-colors',
        className,
      )}
    >
      {children}
    </div>
  )
}

interface MetricCardProps {
  label: string
  value: string | number
  unit?: string
  sub?: string
  color?: 'cyan' | 'purple' | 'green' | 'orange' | 'red'
  icon?: ReactNode
  trend?: number
}

const colorMap = {
  cyan: { text: 'text-[#00d4ff]', bg: 'bg-[rgba(0,212,255,0.08)]', bar: 'bg-[#00d4ff]' },
  purple: { text: 'text-[#7c3aed]', bg: 'bg-[rgba(124,58,237,0.08)]', bar: 'bg-[#7c3aed]' },
  green: { text: 'text-[#22c55e]', bg: 'bg-[rgba(34,197,94,0.08)]', bar: 'bg-[#22c55e]' },
  orange: { text: 'text-[#f59e0b]', bg: 'bg-[rgba(245,158,11,0.08)]', bar: 'bg-[#f59e0b]' },
  red: { text: 'text-[#ef4444]', bg: 'bg-[rgba(239,68,68,0.08)]', bar: 'bg-[#ef4444]' },
}

export function MetricCard({ label, value, unit, sub, color = 'cyan', icon, trend }: MetricCardProps) {
  const c = colorMap[color]
  return (
    <div className="glass rounded-xl p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground font-mono uppercase tracking-wider">{label}</span>
        {icon && <div className={cn('p-1.5 rounded-lg', c.bg, c.text)}>{icon}</div>}
      </div>
      <div className="flex items-end gap-1">
        <span className={cn('text-2xl font-bold font-mono', c.text)}>{value}</span>
        {unit && <span className="text-sm text-muted-foreground mb-0.5">{unit}</span>}
      </div>
      {sub && (
        <div className="flex items-center gap-1.5">
          {trend !== undefined && (
            <span className={cn('text-xs font-mono', trend >= 0 ? 'text-[#22c55e]' : 'text-[#ef4444]')}>
              {trend >= 0 ? '+' : ''}{trend}%
            </span>
          )}
          <span className="text-xs text-muted-foreground">{sub}</span>
        </div>
      )}
    </div>
  )
}

interface StatusBadgeProps {
  status: 'online' | 'offline' | 'busy' | 'idle' | 'error'
  label?: string
  className?: string
}

const statusConfig = {
  online: { dot: 'bg-[#22c55e] animate-pulse-glow', text: 'text-[#22c55e]', label: 'Online' },
  offline: { dot: 'bg-zinc-600', text: 'text-zinc-500', label: 'Offline' },
  busy: { dot: 'bg-[#f59e0b]', text: 'text-[#f59e0b]', label: 'Busy' },
  idle: { dot: 'bg-[#00d4ff]', text: 'text-[#00d4ff]', label: 'Idle' },
  error: { dot: 'bg-[#ef4444]', text: 'text-[#ef4444]', label: 'Error' },
}

export function StatusBadge({ status, label, className }: StatusBadgeProps) {
  const cfg = statusConfig[status]
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <span className={cn('w-1.5 h-1.5 rounded-full', cfg.dot)} />
      <span className={cn('text-xs font-mono', cfg.text)}>{label ?? cfg.label}</span>
    </span>
  )
}

interface ProgressRingProps {
  value: number
  size?: number
  stroke?: number
  color?: string
  label?: string
  className?: string
}

export function ProgressRing({ value, size = 56, stroke = 4, color = '#00d4ff', label, className }: ProgressRingProps) {
  const r = (size - stroke) / 2
  const circ = 2 * Math.PI * r
  const offset = circ - (value / 100) * circ
  return (
    <div className={cn('relative inline-flex items-center justify-center', className)} style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={stroke} />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none"
          stroke={color} strokeWidth={stroke}
          strokeDasharray={circ} strokeDashoffset={offset}
          strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 0.6s ease', filter: `drop-shadow(0 0 4px ${color}66)` }}
        />
      </svg>
      {label && (
        <span className="absolute text-[10px] font-mono font-bold" style={{ color }}>
          {label}
        </span>
      )}
    </div>
  )
}
