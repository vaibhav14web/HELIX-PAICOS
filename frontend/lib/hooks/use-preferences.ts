import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api-client'

export function usePreferences() {
  const queryClient = useQueryClient()

  const all = useQuery({
    queryKey: ['preferences'],
    queryFn: () => api.preferences.getAll(),
  })

  const getByCategory = (category: string) =>
    useQuery({
      queryKey: ['preferences', category],
      queryFn: () => api.preferences.getAll(category),
    })

  const get = (key: string) =>
    useQuery({
      queryKey: ['preference', key],
      queryFn: () => api.preferences.get(key),
    })

  const store = useMutation({
    mutationFn: ({ key, value, category }: { key: string; value: unknown; category?: string }) =>
      api.preferences.store(key, value, category),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['preferences'] })
    },
  })

  return { all, getByCategory, get, store }
}
