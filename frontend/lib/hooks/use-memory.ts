import { useCallback, useMemo } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type WarmMemoryEntry } from '@/lib/api-client'
import { filterMemoryEntries } from '@/lib/memory-view-model'

const EMPTY_ENTRIES: WarmMemoryEntry[] = []

export function useAddMemoryEntry() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: { text: string; category: string }) => api.warmMemory.index(payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['warm-memory'] }),
  })
}

export function useMemoryEntries(searchText: string, category: string) {
  const normalizedSearch = searchText.trim()
  const selectedCategory = category === 'All' ? undefined : category

  const statsQuery = useQuery({
    queryKey: ['warm-memory', 'stats'],
    queryFn: api.warmMemory.stats,
    refetchInterval: 30_000,
  })

  const entriesQuery = useInfiniteQuery({
    queryKey: ['warm-memory', 'entries'],
    queryFn: ({ pageParam }) => api.warmMemory.entries({ limit: 50, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })

  const categoryEntriesQuery = useInfiniteQuery({
    queryKey: ['warm-memory', 'entries', selectedCategory],
    queryFn: ({ pageParam }) => api.warmMemory.entries({ limit: 50, cursor: pageParam, category: selectedCategory }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: !normalizedSearch && Boolean(selectedCategory),
  })

  const searchQuery = useQuery({
    queryKey: ['warm-memory', 'search', normalizedSearch, selectedCategory],
    queryFn: () => api.warmMemory.search({
      query: normalizedSearch,
      category: selectedCategory,
      limit: 100,
      min_score: 0,
    }),
    enabled: Boolean(normalizedSearch),
  })

  const allEntries = useMemo(
    () => entriesQuery.data?.pages.flatMap((page) => page.entries) ?? EMPTY_ENTRIES,
    [entriesQuery.data]
  )
  const categoryEntries = useMemo(
    () => categoryEntriesQuery.data?.pages.flatMap((page) => page.entries) ?? EMPTY_ENTRIES,
    [categoryEntriesQuery.data]
  )
  const listedEntries = selectedCategory ? categoryEntries : allEntries
  const searchEntries = useMemo(
    () => searchQuery.data?.results ?? EMPTY_ENTRIES,
    [searchQuery.data]
  )
  const entries: WarmMemoryEntry[] = useMemo(
    () => (normalizedSearch ? searchEntries : filterMemoryEntries(listedEntries, category)),
    [normalizedSearch, searchEntries, listedEntries, category]
  )
  const categories = useMemo(
    () => Array.from(new Set(allEntries.map((entry) => entry.category))).sort(),
    [allEntries]
  )
  const activeEntriesQuery = selectedCategory ? categoryEntriesQuery : entriesQuery
  const activeQueryError = normalizedSearch ? searchQuery.error : activeEntriesQuery.error

  const retry = useCallback(async () => {
    await Promise.all([
      normalizedSearch ? searchQuery.refetch() : activeEntriesQuery.refetch(),
      statsQuery.refetch(),
    ])
  }, [normalizedSearch, searchQuery, activeEntriesQuery, statsQuery])

  return {
    entries,
    categories,
    isLoading: normalizedSearch ? searchQuery.isLoading : activeEntriesQuery.isLoading,
    isFetching: normalizedSearch ? searchQuery.isFetching : activeEntriesQuery.isFetching,
    error: activeQueryError,
    retry,
    hasNextPage: !normalizedSearch && activeEntriesQuery.hasNextPage,
    fetchNextPage: activeEntriesQuery.fetchNextPage,
    isFetchingNextPage: activeEntriesQuery.isFetchingNextPage,
    statsCount: statsQuery.data?.vector_count ?? null,
    serviceStatus: statsQuery.data?.status ?? null,
    isStatsLoading: statsQuery.isLoading,
    statsError: statsQuery.error,
  }
}
