import { useState } from 'react'

import {
  askQuestion,
  continueConversation,
  formatApiError,
  sendFeedback,
} from '../api/chatApi'
import {
  emptyCascadeSelections,
  RETRIEVAL_METHOD,
} from '../constants/slots'

export default function useChat() {
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [history, setHistory] = useState([])
  const [sessionId, setSessionId] = useState(null)
  const [slotInput, setSlotInput] = useState('')
  const [cascadeSelections, setCascadeSelections] = useState(
    emptyCascadeSelections,
  )

  const awaitingInput =
    result?.status === 'needs_input' || result?.status === 'error'
  const awaitingCascade = result?.status === 'needs_cascade_input'
  const inConversation =
    Boolean(sessionId) && (awaitingInput || awaitingCascade)

  function syncCascadeSelections(data) {
    const selections = data?.cascade?.selections || {}
    setCascadeSelections({
      coverage: selections.coverage || '',
      population: selections.population || '',
      line: selections.line || '',
    })
  }

  function applyAskResponse(data, originalQuestion) {
    setResult(data)
    if (data.status === 'needs_cascade_input') {
      syncCascadeSelections(data)
    }
    if (data.status === 'complete') {
      setSessionId(null)
      setSlotInput('')
      setCascadeSelections(emptyCascadeSelections())
      setHistory(previous => [
        {
          question: originalQuestion || data.question,
          method: data.retrieval_method || RETRIEVAL_METHOD,
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
    setCascadeSelections(emptyCascadeSelections())
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

  async function sendSessionAction(action, value = null) {
    if (!sessionId) return

    setLoading(true)
    setError(null)
    try {
      const data = await continueConversation({
        question: result?.question || '',
        sessionId,
        action,
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
    await sendSessionAction('provide', slotInput.trim())
  }

  async function onCascadeChange(field, value) {
    let next = { ...cascadeSelections, [field]: value }
    if (field === 'coverage') {
      next = { coverage: value, population: '', line: '' }
    } else if (field === 'population') {
      next = { ...next, line: '' }
    }
    setCascadeSelections(next)
    await sendSessionAction('cascade_options', next)
  }

  async function submitCascade() {
    await sendSessionAction('submit_cascade', cascadeSelections)
  }

  function cascadeReady() {
    const pending = result?.cascade?.pending_slots || []
    if (pending.includes('coverage') && !cascadeSelections.coverage) return false
    if (pending.includes('population') && !cascadeSelections.population) {
      return false
    }
    if (pending.includes('line') && !cascadeSelections.line) return false
    return pending.length > 0 || Boolean(cascadeSelections.coverage)
  }

  function startOver() {
    setSessionId(null)
    setResult(null)
    setSlotInput('')
    setCascadeSelections(emptyCascadeSelections())
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
    cascadeSelections,
    awaitingInput,
    awaitingCascade,
    inConversation,
    ask,
    sendSessionAction,
    submitSlotValue,
    onCascadeChange,
    submitCascade,
    cascadeReady,
    submitFeedback: sendFeedback,
    startOver,
    showHistoryItem,
  }
}
