import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api-client'

export function useWork(sessionId = 'default') {
  const queryClient = useQueryClient()

  const context = useQuery({
    queryKey: ['work-context', sessionId],
    queryFn: () => api.work.context(sessionId),
  })

  const tasks = useQuery({
    queryKey: ['work-tasks', sessionId],
    queryFn: () => api.work.tasks(sessionId),
    refetchInterval: 15_000,
  })

  const scratchpad = useQuery({
    queryKey: ['scratchpad', sessionId],
    queryFn: () => api.work.scratchpad.get(sessionId),
  })

  const addTask = useMutation({
    mutationFn: (task: string) => api.work.addTask(sessionId, task),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['work-tasks', sessionId] })
    },
  })

  const saveScratchpad = useMutation({
    mutationFn: (content: string) => api.work.scratchpad.save(sessionId, content),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scratchpad', sessionId] })
    },
  })

  const updateContext = useMutation({
    mutationFn: (updates: Record<string, unknown>) => api.work.updateContext(sessionId, updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['work-context', sessionId] })
    },
  })

  return { context, tasks, scratchpad, addTask, saveScratchpad, updateContext }
}
