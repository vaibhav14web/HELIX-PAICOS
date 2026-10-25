import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type ChatResponse } from '@/lib/api-client'

export function useChat(sessionId = 'default') {
  const queryClient = useQueryClient()

  const history = useQuery({
    queryKey: ['conversation', sessionId],
    queryFn: () => api.chat.history(sessionId),
    refetchInterval: 5_000,
  })

  const sendMessage = useMutation({
    mutationFn: (message: string) => api.chat.send(message, sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conversation', sessionId] })
      queryClient.invalidateQueries({ queryKey: ['conversation_sessions'] })
    },
  })

  const stopGeneration = useMutation({
    mutationFn: () => api.chat.stop(sessionId),
  })

  return {
    history,
    sendMessage,
    stopGeneration,
  }
}
