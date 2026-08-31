import { useState } from 'react'

import {
  askQuestion,
  continueConversation,
  formatApiError,
  sendFeedback,
} from '../api/chatApi'

export default function useChat() {
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [history, setHistory] = useState([])
  const [sessionId, setSessionId] = useState(null)
  const [slotInput, setSlotInput] = useState('')

  const awaitingInput =
    result?.status === 'needs_input' || result?.status === 'error'
  const inConversation = Boolean(sessionId) && awaitingInput

  function applyAskResponse(data, originalQuestion) {
    setResult(data)
    if (data.status === 'complete') {
      setSessionId(null)
      setSlotInput('')
      setHistory(previous => [
        {
          question: originalQuestion || data.question,
          result: data,
        },
        ...previous,
      ])
    } else if (data.session_id) {
      setSessionId(data.session_id)
    }
  }

  async function ask() {
    if (!question.trim()) return

    setLoading(true)
    setError(null)
    setResult(null)
    setSessionId(null)
    setSlotInput('')
    const asked = question.trim()

    try {
      const data = await askQuestion(asked)
      applyAskResponse(data, asked)
      setQuestion('')
    } catch (requestError) {
      setError(formatApiError(requestError.message))
    } finally {
      setLoading(false)
    }
  }

  async function provideSlot(value) {
    if (!sessionId) return

    setLoading(true)
    setError(null)
    try {
      const data = await continueConversation({
        question: result?.question || '',
        sessionId,
        value,
      })
      setSlotInput('')
      applyAskResponse(data, result?.question)
    } catch (requestError) {
      setError(formatApiError(requestError.message))
    } finally {
      setLoading(false)
    }
  }

  async function submitSlotValue() {
    if (!slotInput.trim()) return
    await provideSlot(slotInput.trim())
  }

  function startOver() {
    setSessionId(null)
    setResult(null)
    setSlotInput('')
  }

  function showHistoryItem(item) {
    setResult({ ...item.result, question: item.question })
  }

  return {
    question,
    setQuestion,
    loading,
    result,
    error,
    history,
    slotInput,
    setSlotInput,
    awaitingInput,
    inConversation,
    ask,
    submitSlotValue,
    submitFeedback: sendFeedback,
    startOver,
    showHistoryItem,
  }
}
