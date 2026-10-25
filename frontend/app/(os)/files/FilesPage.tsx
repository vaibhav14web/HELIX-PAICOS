'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { api, ApiError, type NoteItem } from '@/lib/api-client'
import { filterFileItems, getFilesViewState, type FilesCategory } from '@/lib/files-view-model'
import { FileText, FileCode, Image, Archive, Music, Download, Trash2, Eye, Search, Grid3X3, List, HardDrive, Plus, StickyNote, X } from 'lucide-react'
import { GlassCard } from '@/components/os/GlassCard'
import { cn } from '@/lib/utils'

interface FileItem {
  id: string
  name: string
  type: 'document' | 'code' | 'image' | 'archive' | 'audio'
  size?: string
  sizeBytes?: number
  modified: string
  path: string
  filename?: string
  content?: string
  source: 'upload' | 'note'
  summary?: string
}

const iconMap: Record<FileItem['type'], React.ComponentType<{ className?: string }>> = {
  document: FileText,
  code: FileCode,
  image: Image,
  archive: Archive,
  audio: Music,
}

const colorMap: Record<FileItem['type'], string> = {
  document: 'text-[#00d4ff]',
  code: 'text-[#7c3aed]',
  image: 'text-[#22c55e]',
  archive: 'text-zinc-400',
  audio: 'text-[#ef4444]',
}

const quickFilters: Array<{ id: FilesCategory; label: string }> = [
  { id: 'all', label: 'All Files' },
  { id: 'document', label: 'Documents' },
  { id: 'code', label: 'Code' },
  { id: 'image', label: 'Images' },
  { id: 'archive', label: 'Archives' },
  { id: 'audio', label: 'Audio' },
]

const fileTypeForName = (name: string): FileItem['type'] => {
  const extension = name.toLowerCase().match(/\.[^.]+$/)?.[0] ?? ''
  if (['.ts', '.tsx', '.js', '.jsx', '.py', '.json', '.yaml', '.yml', '.toml', '.xml', '.csv'].includes(extension)) return 'code'
  if (['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp'].includes(extension)) return 'image'
  if (['.zip', '.tar', '.gz', '.7z', '.rar'].includes(extension)) return 'archive'
  if (['.mp3', '.wav', '.ogg', '.m4a', '.flac'].includes(extension)) return 'audio'
  return 'document'
}

const formatBytes = (size: number) => {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${(size / 1024 / 1024 / 1024).toFixed(1)} GB`
}

export function FilesPage() {
  const [view, setView] = useState<'grid' | 'list'>('list')
  const [selected, setSelected] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState<FilesCategory>('all')
  const [files, setFiles] = useState<FileItem[]>([])
  const [selectedFile, setSelectedFile] = useState<FileItem | null>(null)
  const [isLoadingFiles, setIsLoadingFiles] = useState(true)
  const [fileLoadError, setFileLoadError] = useState<string | null>(null)
  const [notesLoadError, setNotesLoadError] = useState<string | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [isSavingNote, setIsSavingNote] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [isPreviewLoading, setIsPreviewLoading] = useState(false)
  const [actionMessage, setActionMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)
  const [noteFormOpen, setNoteFormOpen] = useState(false)
  const [noteTitle, setNoteTitle] = useState('')
  const [noteContent, setNoteContent] = useState('')
  const [preview, setPreview] = useState<{ kind: 'text'; title: string; text: string } | { kind: 'frame'; title: string; url: string } | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const loadItems = useCallback(async () => {
    setIsLoadingFiles(true)
    setFileLoadError(null)
    setNotesLoadError(null)
    const [fileResult, noteResult] = await Promise.allSettled([api.files.list(), api.notes.list()])
    const uploads: FileItem[] = fileResult.status === 'fulfilled'
      ? fileResult.value.files.map((file) => ({
          id: `upload:${file.filename}`,
          name: file.name,
          filename: file.filename,
          type: fileTypeForName(file.name),
          size: file.size,
          sizeBytes: file.size_bytes,
          modified: file.modified,
          path: file.path,
          source: 'upload',
          summary: 'Stored in your HELIX uploads.',
        }))
      : []
    const notes: FileItem[] = noteResult.status === 'fulfilled'
      ? noteResult.value.notes.map((note: NoteItem) => ({
          id: `note:${note.filename}`,
          name: `${note.title}.txt`,
          filename: note.filename,
          type: 'document',
          size: formatBytes(note.size_bytes || new Blob([note.content]).size),
          sizeBytes: note.size_bytes || new Blob([note.content]).size,
          modified: new Date(note.updated_at * 1000).toLocaleDateString(),
          path: note.path ?? `Notes/${note.filename}`,
          content: note.content,
          source: 'note',
          summary: note.content.slice(0, 120) || 'An empty note.',
        }))
      : []
    if (fileResult.status === 'rejected') setFileLoadError('Uploaded files could not be loaded.')
    if (noteResult.status === 'rejected') setNotesLoadError('Notes could not be loaded.')
    const loaded = [...notes, ...uploads]
    setFiles(loaded)
    setSelectedFile((current) => current ? loaded.find((file) => file.id === current.id) ?? null : null)
    setSelected((current) => current.filter((id) => loaded.some((file) => file.id === id)))
    setIsLoadingFiles(false)
  }, [])

  useEffect(() => { void loadItems() }, [loadItems])
  useEffect(() => () => {
    if (preview?.kind === 'frame') URL.revokeObjectURL(preview.url)
  }, [preview])

  const filtered = useMemo(() => filterFileItems(files, search, category), [files, search, category])
  const viewState = getFilesViewState({
    isLoading: isLoadingFiles,
    hasError: Boolean(fileLoadError),
    resultCount: filtered.length,
    searchText: search,
    category,
  })
  const uploadBytes = useMemo(() => files.filter((file) => file.source === 'upload')
    .reduce((total, file) => total + (file.sizeBytes ?? 0), 0), [files])

  const handleCreateNote = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!noteTitle.trim()) return
    setIsSavingNote(true)
    setActionMessage(null)
    try {
      await api.notes.create(noteTitle.trim(), noteContent)
      setNoteTitle('')
      setNoteContent('')
      setNoteFormOpen(false)
      await loadItems()
      setActionMessage({ kind: 'success', text: 'Note created.' })
    } catch (error) {
      const message = error instanceof ApiError && error.status === 409
        ? 'A note with this title already exists.'
        : 'Could not save the note. Please try again.'
      setActionMessage({ kind: 'error', text: message })
    } finally {
      setIsSavingNote(false)
    }
  }

  const toggle = (id: string) => setSelected((s) => s.includes(id) ? s.filter((x) => x !== id) : [...s, id])

  const handleSelect = (file: FileItem) => {
    setSelectedFile(file)
    setSelected([file.id])
  }

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = event.target.files
    if (!fileList?.length) return
    setIsUploading(true)
    setActionMessage(null)
    let uploadedCount = 0
    const failures: string[] = []
    for (const file of Array.from(fileList)) {
      const formData = new FormData()
      formData.append('file', file)
      try {
        await api.files.upload(formData)
        uploadedCount += 1
      } catch { failures.push(file.name) }
    }
    event.target.value = ''
    await loadItems()
    setIsUploading(false)
    setActionMessage(failures.length
      ? { kind: 'error', text: `${uploadedCount} uploaded; could not upload ${failures.join(', ')}.` }
      : { kind: 'success', text: `${uploadedCount} file${uploadedCount === 1 ? '' : 's'} uploaded.` })
  }

  const handleDelete = async () => {
    if (!selected.length) return
    const targets = files.filter((file) => selected.includes(file.id))
    if (!window.confirm(`Delete ${targets.length} selected item${targets.length === 1 ? '' : 's'}? This cannot be undone.`)) return
    setIsDeleting(true)
    setActionMessage(null)
    const failures: string[] = []
    for (const file of targets) {
      try {
        if (file.source === 'note') await api.notes.delete(file.filename ?? file.name)
        else await api.files.delete(file.filename ?? file.name)
      } catch { failures.push(file.name) }
    }
    setSelected([])
    await loadItems()
    setIsDeleting(false)
    setActionMessage(failures.length
      ? { kind: 'error', text: `Could not delete ${failures.join(', ')}.` }
      : { kind: 'success', text: `${targets.length} item${targets.length === 1 ? '' : 's'} deleted.` })
  }

  const handleDownload = async (file: FileItem) => {
    setActionMessage(null)
    try {
      const blob = file.source === 'note'
        ? new Blob([file.content ?? ''], { type: 'text/plain;charset=utf-8' })
        : await api.files.download(file.filename ?? file.name)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = file.name
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {
      setActionMessage({ kind: 'error', text: `Could not download ${file.name}.` })
    }
  }

  const handlePreview = async (file: FileItem) => {
    setActionMessage(null)
    if (file.source === 'note') {
      setPreview({ kind: 'text', title: file.name, text: file.content ?? '' })
      return
    }
    const extension = file.name.toLowerCase().match(/\.[^.]+$/)?.[0] ?? ''
    const textExtensions = ['.txt', '.md', '.json', '.js', '.jsx', '.ts', '.tsx', '.py', '.yaml', '.yml', '.toml', '.xml', '.csv', '.svg']
    const frameExtensions = ['.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']
    if (!textExtensions.includes(extension) && !frameExtensions.includes(extension)) {
      setActionMessage({ kind: 'error', text: 'Preview is not available for this file type. Use Download instead.' })
      return
    }
    setIsPreviewLoading(true)
    try {
      const blob = await api.files.download(file.filename ?? file.name)
      if (textExtensions.includes(extension)) setPreview({ kind: 'text', title: file.name, text: await blob.text() })
      else setPreview({ kind: 'frame', title: file.name, url: URL.createObjectURL(blob) })
    } catch {
      setActionMessage({ kind: 'error', text: `Could not open ${file.name}.` })
    } finally {
      setIsPreviewLoading(false)
    }
  }

  return (
    <div className="flex h-full">
      <div className="w-56 shrink-0 border-r border-[rgba(255,255,255,0.06)] flex flex-col">
        <div className="p-4 border-b border-[rgba(255,255,255,0.06)]">
          <h3 className="text-xs font-mono uppercase tracking-widest text-zinc-600 mb-3">Your Storage</h3>
          <GlassCard className="space-y-2">
            <div className="flex items-center gap-2">
              <HardDrive className="w-4 h-4 text-[#00d4ff]" />
              <div>
                <div className="text-sm font-bold font-mono text-zinc-200">{formatBytes(uploadBytes)}</div>
                <div className="text-[10px] text-zinc-600">used by uploaded files</div>
              </div>
            </div>
            <div className="text-xs text-zinc-500">{isLoadingFiles ? 'Loading item count…' : fileLoadError && notesLoadError ? 'Item count unavailable' : `${files.length} item${files.length === 1 ? '' : 's'} available`}</div>
          </GlassCard>
        </div>
        <div className="p-3 flex-1">
          <h3 className="text-xs font-mono uppercase tracking-widest text-zinc-600 mb-2">Browse by Type</h3>
          {quickFilters.map((filter) => {
            const Icon = filter.id === 'all' ? FileText : iconMap[filter.id]
            const active = category === filter.id
            return (
              <button
                key={filter.id}
                aria-pressed={active}
                onClick={() => setCategory(filter.id)}
                className={cn('w-full flex items-center gap-2 px-2 py-2 rounded-lg text-left text-xs transition-colors', active ? 'text-[#00d4ff] bg-[rgba(0,212,255,0.08)]' : 'text-zinc-500 hover:text-zinc-200 hover:bg-[rgba(255,255,255,0.03)]')}
              >
                <Icon className="w-3.5 h-3.5" />
                {filter.label}
              </button>
            )
          })}
        </div>
      </div>

      <div className="flex-1 flex flex-col min-w-0">
        <div className="flex items-center gap-3 px-4 py-3 border-b border-[rgba(255,255,255,0.06)] shrink-0">
          <h2 className="text-sm font-medium text-zinc-300">{quickFilters.find((filter) => filter.id === category)?.label ?? 'All Files'}</h2>
          <div className="flex-1" />
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-600" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search files..."
              className="pl-8 pr-3 py-1.5 bg-[rgba(255,255,255,0.04)] border border-[rgba(255,255,255,0.07)] rounded-lg text-xs text-zinc-300 placeholder:text-zinc-600 outline-none focus:border-[rgba(0,212,255,0.3)] w-40 transition-colors"
            />
          </div>
          <div className="flex items-center gap-1 bg-[rgba(255,255,255,0.04)] rounded-lg p-1">
            {([['list', List], ['grid', Grid3X3]] as const).map(([v, Icon]) => (
              <button key={v} onClick={() => setView(v)} className={cn('p-1.5 rounded transition-colors', view === v ? 'bg-[rgba(0,212,255,0.1)] text-[#00d4ff]' : 'text-zinc-600 hover:text-zinc-300')}>
                <Icon className="w-3.5 h-3.5" />
              </button>
            ))}
          </div>
          <input ref={fileInputRef} type="file" multiple className="hidden" onChange={handleUpload} />
          <button onClick={() => setNoteFormOpen(true)} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[rgba(34,197,94,0.08)] border border-[rgba(34,197,94,0.2)] text-[#22c55e] text-xs font-medium hover:bg-[rgba(34,197,94,0.12)] transition-colors">
            <StickyNote className="w-3.5 h-3.5" /> New Note
          </button>
          <button disabled={isUploading} onClick={() => fileInputRef.current?.click()} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[rgba(0,212,255,0.08)] border border-[rgba(0,212,255,0.2)] text-[#00d4ff] text-xs font-medium hover:bg-[rgba(0,212,255,0.12)] transition-colors disabled:opacity-50">
            <Plus className="w-3.5 h-3.5" /> {isUploading ? 'Uploading…' : 'Upload'}
          </button>
        </div>

        {actionMessage && (
          <div role={actionMessage.kind === 'error' ? 'alert' : 'status'} className={cn('px-4 py-2 text-xs', actionMessage.kind === 'error' ? 'text-rose-300 bg-rose-500/5' : 'text-emerald-300 bg-emerald-500/5')}>
            {actionMessage.text}
          </div>
        )}
        {notesLoadError && <div role="status" className="px-4 py-2 text-xs text-amber-300">{notesLoadError}</div>}
        {fileLoadError && files.length > 0 && <div role="alert" className="px-4 py-2 text-xs text-rose-300">{fileLoadError} Showing available notes. <button onClick={() => void loadItems()} className="underline">Try again</button></div>}

        {selected.length > 0 && (
          <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} className="flex items-center gap-3 px-4 py-2 bg-[rgba(0,212,255,0.05)] border-b border-[rgba(0,212,255,0.1)]">
            <span className="text-xs text-zinc-400">{selected.length} selected</span>
            <button disabled={isDeleting} onClick={handleDelete} className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs text-zinc-400 hover:text-zinc-200 hover:bg-[rgba(255,255,255,0.05)] transition-colors disabled:opacity-50">
              <Trash2 className="w-3.5 h-3.5" /> {isDeleting ? 'Deleting…' : 'Delete'}
            </button>
          </motion.div>
        )}

        <div className="flex-1 overflow-y-auto p-4">
          {viewState === 'loading' ? (
            <div className="py-12 text-center text-sm text-zinc-500">Loading your files…</div>
          ) : viewState === 'error' ? (
            <div role="alert" className="py-12 text-center text-sm text-rose-300">
              <p>{fileLoadError}</p>
              <button onClick={() => void loadItems()} className="mt-3 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-300 hover:bg-white/5">Try again</button>
            </div>
          ) : viewState !== 'results' ? (
            <div className="py-12 text-center text-sm text-zinc-500">
              {viewState === 'empty-search' ? 'No files match your search.' : viewState === 'empty-category' ? `No ${quickFilters.find((filter) => filter.id === category)?.label.toLowerCase()} found.` : 'No files yet. Upload a file or create a note to get started.'}
            </div>
          ) : view === 'list' ? (
            <div className="space-y-1">
              <div className="grid grid-cols-[auto,1fr,80px,100px,80px] gap-4 px-3 py-1.5 text-[10px] font-mono uppercase tracking-wider text-zinc-700">
                <span className="w-4" />
                <span>Name</span>
                <span className="text-right">Size</span>
                <span>Modified</span>
                <span />
              </div>
              {filtered.map((file, i) => {
                const Icon = iconMap[file.type]
                const color = colorMap[file.type]
                const isSelected = selected.includes(file.id)
                return (
                  <motion.div
                    key={file.id}
                    initial={{ opacity: 0, x: -4 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: i * 0.03 }}
                    onClick={() => handleSelect(file)}
                    className={cn(
                      'grid grid-cols-[auto,1fr,80px,100px,80px] gap-4 items-center px-3 py-2.5 rounded-lg cursor-pointer transition-colors group',
                      isSelected ? 'bg-[rgba(0,212,255,0.07)]' : 'hover:bg-[rgba(255,255,255,0.03)]',
                    )}
                  >
                    <input type="checkbox" checked={isSelected} onChange={() => toggle(file.id)} onClick={(e) => e.stopPropagation()} className="w-3.5 h-3.5 accent-[#00d4ff]" />
                    <div className="flex items-center gap-2.5 min-w-0">
                      <Icon className={cn('w-4 h-4 shrink-0', color)} />
                      <span className="text-sm text-zinc-300 truncate">{file.name}</span>
                      {file.source === 'note' && <span className="text-[10px] text-zinc-700 shrink-0">Note</span>}
                    </div>
                    <div className="text-xs text-zinc-600 font-mono text-right">{file.size ?? '—'}</div>
                    <div className="text-xs text-zinc-600 font-mono">{file.modified}</div>
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button aria-label={`Preview ${file.name}`} title="Preview" onClick={(e) => { e.stopPropagation(); void handlePreview(file) }} className="p-1 rounded hover:text-zinc-200 text-zinc-600 transition-colors"><Eye className="w-3.5 h-3.5" /></button>
                      <button aria-label={`Download ${file.name}`} title="Download" onClick={(e) => { e.stopPropagation(); void handleDownload(file) }} className="p-1 rounded hover:text-zinc-200 text-zinc-600 transition-colors"><Download className="w-3.5 h-3.5" /></button>
                    </div>
                  </motion.div>
                )
              })}
            </div>
          ) : (
            <div className="grid grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
              {filtered.map((file, i) => {
                const Icon = iconMap[file.type]
                const color = colorMap[file.type]
                const isSelected = selected.includes(file.id)
                return (
                  <motion.div
                    key={file.id}
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ delay: i * 0.03 }}
                    onClick={() => handleSelect(file)}
                    className={cn(
                      'flex flex-col items-center gap-2 p-4 rounded-xl cursor-pointer transition-colors text-center',
                      isSelected ? 'bg-[rgba(0,212,255,0.07)] border border-[rgba(0,212,255,0.2)]' : 'glass hover:border-[rgba(255,255,255,0.12)]',
                    )}
                  >
                    <Icon className={cn('w-8 h-8', color)} />
                    <span className="text-xs text-zinc-400 truncate w-full">{file.name}</span>
                    <span className="text-[10px] text-zinc-700 font-mono">{file.size ?? '—'}</span>
                  </motion.div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      <div className="w-80 shrink-0 border-l border-[rgba(255,255,255,0.06)] bg-[rgba(255,255,255,0.02)] p-4">
        {selectedFile ? (
          <div className="space-y-4">
            <div>
              <p className="text-[10px] font-mono uppercase tracking-widest text-zinc-600">Selected Item</p>
              <h3 className="text-lg font-semibold text-zinc-100 mt-1">{selectedFile.name}</h3>
              <p className="text-sm text-zinc-500 mt-2">{selectedFile.summary ?? 'No summary available.'}</p>
            </div>
            <GlassCard className="space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span className="text-zinc-500">Path</span>
                <span className="text-zinc-300 font-mono text-xs">{selectedFile.path}</span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-zinc-500">Modified</span>
                <span className="text-zinc-300">{selectedFile.modified}</span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-zinc-500">Type</span>
                <span className="text-zinc-300 capitalize">{selectedFile.type}</span>
              </div>
            </GlassCard>
            <div className="flex gap-2">
              <button disabled={isPreviewLoading} onClick={() => void handlePreview(selectedFile)} className="flex-1 rounded-lg border border-[rgba(0,212,255,0.2)] bg-[rgba(0,212,255,0.08)] px-3 py-2 text-sm text-[#00d4ff] disabled:opacity-50">{isPreviewLoading ? 'Opening…' : 'Preview'}</button>
              <button onClick={() => void handleDownload(selectedFile)} className="flex-1 flex items-center justify-center gap-1.5 rounded-lg border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)] px-3 py-2 text-sm text-zinc-300"><Download className="w-3.5 h-3.5" /> Download</button>
            </div>
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-center text-sm text-zinc-500">
            Select a file to inspect it.
          </div>
        )}
      </div>

      {noteFormOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <form onSubmit={handleCreateNote} role="dialog" aria-modal="true" aria-labelledby="new-note-title" className="w-full max-w-lg rounded-2xl border border-white/10 bg-zinc-950 p-5 shadow-2xl">
            <div className="mb-4 flex items-center justify-between">
              <h2 id="new-note-title" className="text-base font-semibold text-zinc-100">Create a note</h2>
              <button type="button" aria-label="Close note form" onClick={() => setNoteFormOpen(false)} className="rounded p-1 text-zinc-500 hover:text-zinc-200"><X className="h-4 w-4" /></button>
            </div>
            <label className="mb-3 block text-xs text-zinc-400">Title
              <input autoFocus required maxLength={100} value={noteTitle} onChange={(event) => setNoteTitle(event.target.value)} className="mt-1 w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-cyan-400/40" />
            </label>
            <label className="mb-4 block text-xs text-zinc-400">Content
              <textarea maxLength={1_048_576} rows={8} value={noteContent} onChange={(event) => setNoteContent(event.target.value)} className="mt-1 w-full resize-y rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-cyan-400/40" />
            </label>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setNoteFormOpen(false)} className="rounded-lg px-3 py-2 text-sm text-zinc-400 hover:bg-white/5">Cancel</button>
              <button type="submit" disabled={isSavingNote || !noteTitle.trim()} className="rounded-lg bg-emerald-500/15 px-4 py-2 text-sm text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50">{isSavingNote ? 'Saving…' : 'Save Note'}</button>
            </div>
          </form>
        </div>
      )}

      {preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4" onClick={() => setPreview(null)}>
          <section role="dialog" aria-modal="true" aria-labelledby="file-preview-title" onClick={(event) => event.stopPropagation()} className="flex h-[min(85vh,800px)] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-white/10 bg-zinc-950 shadow-2xl">
            <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
              <h2 id="file-preview-title" className="truncate text-sm font-medium text-zinc-100">{preview.title}</h2>
              <button aria-label="Close preview" onClick={() => setPreview(null)} className="rounded p-1 text-zinc-500 hover:text-zinc-200"><X className="h-4 w-4" /></button>
            </div>
            {preview.kind === 'text'
              ? <pre className="flex-1 overflow-auto whitespace-pre-wrap break-words p-5 text-sm text-zinc-300">{preview.text}</pre>
              : <iframe title={`Preview of ${preview.title}`} src={preview.url} sandbox="" className="min-h-0 flex-1 bg-white" />}
          </section>
        </div>
      )}

    </div>
  )
}
