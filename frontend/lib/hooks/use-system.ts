import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api-client'

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    refetchInterval: 15_000,
  })
}

export function useSystemProfile() {
  return useQuery({
    queryKey: ['system-profile'],
    queryFn: api.system.profile,
    staleTime: 300_000,
  })
}

export function useSystemStats() {
  return useQuery({
    queryKey: ['system-stats'],
    queryFn: api.system.stats,
    refetchInterval: 30_000,
  })
}

export function useSystemState() {
  return useQuery({
    queryKey: ['system-state'],
    queryFn: api.state,
    refetchInterval: 10_000,
  })
}
