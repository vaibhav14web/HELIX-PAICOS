import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api-client'

export function useAggregatedContext(sessionId = 'default', force = false) {
  return useQuery({
    queryKey: ['aggregated-context', sessionId, force],
    queryFn: () => api.context.aggregated(sessionId, force),
    refetchInterval: 30_000,
  })
}
