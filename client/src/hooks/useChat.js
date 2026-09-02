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
  const [activeQuestion, setActiveQuestion] = useState('')
  const [slotTurns, setSlotTurns] = useState([])

  const awaitingInput =
    result?.status === 'needs_input' || result?.status === 'error'
  const inConversation = Boolean(sessionId) && awaitingInput

  function applyAskResponse(data, originalQuestion, completedTurns = []) {
    setResult(data)
    if (data.status === 'complete') {
      setSessionId(null)
      setSlotInput('')
      setHistory(previous => [
        {
          question: originalQuestion || data.question,
          result: data,
          slotTurns: completedTurns,
        },
        ...previous,
      ])
    } else if (data.session_id) {
      setSessionId(data.session_id)
    }
  }

  async function ask(questionOverride, guidedIntent = null, guidedSlots = null) {
    const nextQuestion =
      typeof questionOverride === 'string' ? questionOverride : question
    if (!nextQuestion.trim()) return

    setLoading(true)
    setError(null)
    setResult(null)
    setSessionId(null)
    setSlotInput('')
    setSlotTurns([])
    const asked = nextQuestion.trim()
    setActiveQuestion(asked)

    try {
      const data = await askQuestion(asked, guidedIntent, guidedSlots)
      applyAskResponse(data, asked, [])
      setQuestion('')
    } catch (requestError) {
      setError(formatApiError(requestError.message))
    } finally {
      setLoading(false)
    }
  }

  async function provideSlot(value) {
    if (!sessionId) return

    const turn = {
      prompt: result?.message || result?.answer,
      slot: result?.current_slot,
      value,
    }
    const nextTurns = [...slotTurns, turn]
    setSlotTurns(nextTurns)
    setLoading(true)
    setError(null)
    try {
      const data = await continueConversation({
        question: result?.question || '',
        sessionId,
        value,
      })
      setSlotInput('')
      applyAskResponse(data, result?.question, nextTurns)
    } catch (requestError) {
      setSlotTurns(previous => previous.slice(0, -1))
      setError(formatApiError(requestError.message))
    } finally {
      setLoading(false)
    }
  }

  async function submitSlotValue(valueOverride) {
    const value =
      typeof valueOverride === 'string' ? valueOverride : slotInput.trim()
    if (!value) return
    setSlotInput(value)
    await provideSlot(value)
  }

  function startOver() {
    setSessionId(null)
    setResult(null)
    setSlotInput('')
    setActiveQuestion('')
    setSlotTurns([])
  }

  function showHistoryItem(item) {
    setActiveQuestion(item.question)
    setResult({ ...item.result, question: item.question })
    setSlotTurns(item.slotTurns || [])
  }

  return {
    question,
    setQuestion,
    activeQuestion,
    slotTurns,
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
