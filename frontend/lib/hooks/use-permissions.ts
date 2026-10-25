import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type PermissionRequestPayload } from '@/lib/api-client'

export function usePermissions() {
  const queryClient = useQueryClient()

  const history = useQuery({
    queryKey: ['permission-history'],
    queryFn: api.permissions.history,
  })

  const pending = useQuery({
    queryKey: ['permission-pending'],
    queryFn: api.permissions.pending,
    refetchInterval: 2_000,
  })

  const patterns = useQuery({
    queryKey: ['permission-patterns'],
    queryFn: api.permissions.patterns,
  })

  const requestPermission = useMutation({
    mutationFn: (payload: PermissionRequestPayload) => api.permissions.request(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['permission-pending'] })
    },
  })

  const grant = useMutation({
    mutationFn: (id: string) => api.permissions.grant(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['permission-pending'] })
      queryClient.invalidateQueries({ queryKey: ['permission-history'] })
    },
  })

  const deny = useMutation({
    mutationFn: (id: string) => api.permissions.deny(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['permission-pending'] })
      queryClient.invalidateQueries({ queryKey: ['permission-history'] })
    },
  })

  const addAutoApprove = useMutation({
    mutationFn: (pattern: string) => api.permissions.addAutoApprove(pattern),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['permission-patterns'] })
    },
  })

  const removeAutoApprove = useMutation({
    mutationFn: (pattern: string) => api.permissions.removeAutoApprove(pattern),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['permission-patterns'] })
    },
  })

  return { history, pending, patterns, requestPermission, grant, deny, addAutoApprove, removeAutoApprove }
}
