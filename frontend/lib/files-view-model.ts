export type FilesCategory = 'all' | 'document' | 'code' | 'image' | 'archive' | 'audio'

export interface FileFilterItem {
  name: string
  type: Exclude<FilesCategory, 'all'>
}

export type FilesViewState = 'loading' | 'error' | 'empty-search' | 'empty-category' | 'empty' | 'results'

export function filterFileItems<T extends FileFilterItem>(files: T[], searchText: string, category: FilesCategory): T[] {
  const query = searchText.trim().toLocaleLowerCase()
  return files.filter((file) =>
    (category === 'all' || file.type === category) && (!query || file.name.toLocaleLowerCase().includes(query)),
  )
}

export function getFilesViewState(input: {
  isLoading: boolean
  hasError: boolean
  resultCount: number
  searchText: string
  category: FilesCategory
}): FilesViewState {
  if (input.isLoading) return 'loading'
  if (input.hasError && input.resultCount === 0) return 'error'
  if (input.resultCount > 0) return 'results'
  if (input.searchText.trim()) return 'empty-search'
  if (input.category !== 'all') return 'empty-category'
  return 'empty'
}
