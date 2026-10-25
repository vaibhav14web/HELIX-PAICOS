export type MemoryViewState = 'loading' | 'error' | 'empty-search' | 'empty-category' | 'empty' | 'results'

export function getMemoryViewState(input: {
  isLoading: boolean
  hasError: boolean
  entryCount: number
  searchText: string
  category: string
}): MemoryViewState {
  if (input.hasError && input.entryCount === 0) return 'error'
  if (input.isLoading && input.entryCount === 0) return 'loading'
  if (input.entryCount > 0) return 'results'
  if (input.searchText.trim()) return 'empty-search'
  if (input.category !== 'All') return 'empty-category'
  return 'empty'
}

export function filterMemoryEntries<T extends { category: string }>(entries: T[], category: string): T[] {
  return category === 'All' ? entries : entries.filter((entry) => entry.category === category)
}
