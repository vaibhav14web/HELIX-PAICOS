import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api-client'

interface SystemMetrics {
  cpu_percent: number
  memory: { total: number; used: number; available: number; percent: number }
  disk?: { total: number; used: number; free: number; percent: number }
  gpu?: { available: boolean; utilization_percent?: number; temperature_c?: number }
  is_idle?: boolean
}

export function useSystemMetrics() {
  return useQuery({
    queryKey: ['system-metrics'],
    queryFn: async () => {
      const res = await api.context.aggregated('default')
      return (res.context?.system ?? {}) as SystemMetrics
    },
    refetchInterval: 10_000,
    retry: 2,
  })
}
