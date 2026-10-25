'use client'

import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Shield, AlertTriangle, Check, X, ShieldCheck } from 'lucide-react'
import { usePermissions } from '@/lib/hooks/use-permissions'
import { cn } from '@/lib/utils'

const actionLabels: Record<string, string> = {
  launch_application: 'Open an application',
  read_file: 'Read a file',
  read_document: 'Read a document',
  write_file: 'Write a file',
  create_file: 'Create a file',
  edit_docx: 'Edit a document',
  generate_pdf: 'Create a PDF',
  inspect_directory: 'Inspect a folder',
  scan_laptop_files: 'Search your files',
  search_projects: 'Search your projects',
  compose_email: 'Draft an email',
  send_email: 'Send an email',
  open_url: 'Open a website',
  send_notification: 'Show a notification',
}

function getActionLabel(action: string) {
  return actionLabels[action] ?? action.replace(/[_:.-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function getRiskLabel(risk?: string) {
  switch (risk?.toLowerCase()) {
    case 'low': return 'Low risk'
    case 'medium': return 'Moderate risk'
    case 'high': return 'High risk'
    case 'critical': return 'Critical risk'
    default: return 'Risk not rated'
  }
}

export function PermissionModal() {
  const { pending, grant, deny, addAutoApprove } = usePermissions()
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(new Set())

  const activeRequest = pending.data?.pending?.find((request) => !dismissedIds.has(request.id))
  const pendingCount = pending.data?.pending?.length ?? 0

  if (!activeRequest) return null

  const parameters = activeRequest.params ?? {}
  const details = [
    { label: 'Application', value: parameters.application },
    { label: 'Website', value: parameters.url },
    { label: 'File or folder', value: parameters.path ?? parameters.folder ?? activeRequest.target },
    { label: 'Search for', value: parameters.query },
    { label: 'Recipient', value: parameters.recipient },
    { label: 'Subject', value: parameters.subject },
    { label: 'Requested change', value: parameters.action },
    { label: 'Content preview', value: parameters.content },
    { label: 'Message preview', value: parameters.body ?? parameters.message },
    { label: 'Text to replace', value: parameters.search_text },
    { label: 'Replacement text', value: parameters.replace_text },
  ].filter((detail): detail is { label: string; value: string | number } =>
    typeof detail.value === 'string' || typeof detail.value === 'number'
  )
  const risk = activeRequest.risk_level?.toLowerCase()
  const riskClass = risk === 'high' || risk === 'critical'
    ? 'text-red-300 bg-red-400/10 border-red-400/25'
    : risk === 'medium'
      ? 'text-amber-200 bg-amber-400/10 border-amber-400/25'
      : risk === 'low'
        ? 'text-emerald-200 bg-emerald-400/10 border-emerald-400/25'
        : 'text-zinc-300 bg-white/5 border-white/10'

  const dismissIfFailed = (id: string) => setDismissedIds((prev) => {
    const next = new Set(prev)
    next.delete(id)
    return next
  })

  const handleGrant = () => {
    setDismissedIds((prev) => new Set(prev).add(activeRequest.id))
    grant.mutate(activeRequest.id, { onError: () => dismissIfFailed(activeRequest.id) })
  }

  const handleDeny = () => {
    setDismissedIds((prev) => new Set(prev).add(activeRequest.id))
    deny.mutate(activeRequest.id, { onError: () => dismissIfFailed(activeRequest.id) })
  }

  const handleAlwaysAllow = () => {
    setDismissedIds((prev) => new Set(prev).add(activeRequest.id))
    addAutoApprove.mutate(activeRequest.action, {
      onSuccess: () => grant.mutate(activeRequest.id),
      onError: () => grant.mutate(activeRequest.id),
    })
  }

  return (
    <AnimatePresence>
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
        <motion.div
          initial={{ opacity: 0, scale: 0.95, y: 10 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: 10 }}
          transition={{ duration: 0.25, ease: 'easeOut' }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="permission-title"
          className="w-full max-w-xl overflow-hidden rounded-2xl border border-[rgba(255,255,255,0.1)] bg-[#0c0c0e]/95 backdrop-blur-md shadow-2xl"
        >
          <div className="flex items-start gap-3 p-5 border-b border-white/[0.07] bg-amber-400/[0.04]">
            <div className="mt-0.5 p-2 rounded-xl bg-amber-400/10 border border-amber-400/20">
              <Shield className="w-5 h-5 text-amber-300" />
            </div>
            <div className="min-w-0 flex-1">
              <h2 id="permission-title" className="text-base font-semibold text-zinc-100">
                HELIX needs your approval
              </h2>
              <p className="mt-1 text-xs text-zinc-400">
                Review the action and what it affects before you decide.
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className={cn('rounded-full border px-2.5 py-1 text-[11px] font-medium', riskClass)}>
                  {getRiskLabel(activeRequest.risk_level)}
                </span>
                {pendingCount > 1 && (
                  <span className="text-[11px] text-zinc-500">
                    {pendingCount} requests are waiting · reviewing one at a time
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="p-5 space-y-4 max-h-[65vh] overflow-y-auto">
            <section className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-4">
              <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">Action requested</h3>
              <p className="mt-1.5 text-sm font-medium text-zinc-100">{getActionLabel(activeRequest.action)}</p>
              {activeRequest.target && !details.some((detail) => detail.value === activeRequest.target) && (
                <p className="mt-2 break-all text-xs text-zinc-400">Affects: {activeRequest.target}</p>
              )}
              {details.length > 0 && (
                <dl className="mt-3 space-y-2 border-t border-white/[0.06] pt-3">
                  {details.map((detail) => (
                    <div key={detail.label} className="grid grid-cols-[100px_minmax(0,1fr)] gap-3 text-xs">
                      <dt className="text-zinc-500">{detail.label}</dt>
                      <dd className={cn(
                        'min-w-0 break-words text-zinc-300',
                        detail.label.toLowerCase().includes('preview') && 'whitespace-pre-wrap line-clamp-4'
                      )}>{String(detail.value)}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </section>

            <section>
              <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">Why HELIX is asking</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-zinc-300">
                {activeRequest.reasoning || 'No explanation was provided for this request.'}
              </p>
            </section>

            {(activeRequest.benefits?.length || activeRequest.risks?.length) ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {Boolean(activeRequest.benefits?.length) && (
                  <section className="rounded-xl border border-emerald-400/15 bg-emerald-400/[0.035] p-3">
                    <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold text-emerald-300">
                      <Check className="h-3.5 w-3.5" /> Possible benefit
                    </h3>
                    <ul className="space-y-1.5 text-xs leading-relaxed text-zinc-300">
                      {activeRequest.benefits.map((benefit, index) => <li key={index}>{benefit}</li>)}
                    </ul>
                  </section>
                )}
                <section className="rounded-xl border border-red-400/15 bg-red-400/[0.035] p-3">
                  <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold text-red-300">
                    <AlertTriangle className="h-3.5 w-3.5" /> Risks to consider
                  </h3>
                  {activeRequest.risks?.length ? (
                    <ul className="space-y-1.5 text-xs leading-relaxed text-zinc-300">
                      {activeRequest.risks.map((item, index) => <li key={index}>{item}</li>)}
                    </ul>
                  ) : (
                    <p className="text-xs text-zinc-400">No specific risks were provided.</p>
                  )}
                </section>
              </div>
            ) : (
              <section className="rounded-xl border border-red-400/15 bg-red-400/[0.035] p-3">
                <h3 className="flex items-center gap-1.5 text-[11px] font-semibold text-red-300">
                  <AlertTriangle className="h-3.5 w-3.5" /> Risks to consider
                </h3>
                <p className="mt-1.5 text-xs text-zinc-400">No specific risks were provided.</p>
              </section>
            )}

            {activeRequest.alternatives?.length ? (
              <section>
                <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">Other options considered</h3>
                <ul className="mt-1.5 space-y-1 text-xs leading-relaxed text-zinc-400">
                  {activeRequest.alternatives.map((alternative, index) => <li key={index}>{alternative}</li>)}
                </ul>
              </section>
            ) : null}
          </div>

          <div className="border-t border-white/[0.07] bg-black/20 p-4">
            <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <button
                onClick={handleAlwaysAllow}
                className="flex items-center justify-center gap-1.5 self-start rounded-lg px-2 py-1.5 text-xs font-medium text-zinc-300 hover:bg-white/[0.05] hover:text-white transition-colors"
              >
                <ShieldCheck className="h-4 w-4 text-emerald-300" />
                Always allow this kind of action
              </button>
              <p className="max-w-xs text-[10px] leading-relaxed text-zinc-500 sm:text-right">
                Future matching requests may run without asking you again.
              </p>
            </div>
            <div className="flex items-center justify-end gap-2">
              <button
                onClick={handleDeny}
                className="flex items-center justify-center gap-1.5 rounded-lg border border-red-400/20 bg-red-400/[0.07] px-4 py-2 text-xs font-semibold text-red-300 hover:bg-red-400/[0.12] transition-colors"
              >
                <X className="h-3.5 w-3.5" /> Deny
              </button>
              <button
                onClick={handleGrant}
                className="flex items-center justify-center gap-1.5 rounded-lg bg-[#00d4ff] px-5 py-2 text-xs font-bold text-[#09090b] hover:bg-[#00b0d4] transition-colors shadow-[0_0_12px_rgba(0,212,255,0.25)]"
              >
                <Check className="h-3.5 w-3.5" /> Allow once
              </button>
            </div>
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  )
}