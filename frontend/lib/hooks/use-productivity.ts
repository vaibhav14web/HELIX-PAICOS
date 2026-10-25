import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api-client'

export function useProductivity(status?: string) {
  const patterns = useQuery({
    queryKey: ['productivity-patterns'],
    queryFn: api.productivity.patterns,
  })

  const suggestions = useQuery({
    queryKey: ['productivity-suggestions', status],
    queryFn: () => api.productivity.suggestions(status),
  })

  return { patterns, suggestions }
}
