import { useEffect, useRef, useState } from 'react'
import BatchEval from './BatchEval'
import { downloadJobResults } from './evalUtils'

const RETRIEVAL_METHOD = 'exact_match_entity_linker'

const TABS = [
  { id: 'chat', label: 'Chat' },
  { id: 'batch', label: 'Batch Eval' },
]

const SLOT_LABELS = {
  regimen: 'Product',
  indication: 'Indication',
  book: 'Book',
  coverage: 'Coverage',
  organization: 'Payer',
  population: 'Subtype',
  line: 'Line of therapy',
}

function formatAwsError(message) {
  if (/token has expired|sso|credentials/i.test(message)) {
    return 'AWS SSO session expired. In your terminal run: aws sso login — then restart the server with python main.py.'
  }
  return message
}

function parseErrorDetail(data, fallback) {
  if (!data?.detail) return fallback
  if (typeof data.detail === 'string') return formatAwsError(data.detail)
  if (Array.isArray(data.detail)) return formatAwsError(data.detail.map(item => item.msg || item).join(', '))
  return formatAwsError(String(data.detail))
}

const SLOT_DISPLAY_ORDER = [
  'regimen',
  'indication',
  'book',
  'organization',
  'coverage',
  'population',
  'line',
]

function ConfirmedSlots({ slots, asList = false }) {
  const entries = SLOT_DISPLAY_ORDER
    .filter(key => slots?.[key])
    .map(key => [key, slots[key]])
  if (!entries.length) return null

  if (asList) {
    return (
      <ul className="text-sm text-gray-700 space-y-1 mb-3">
        {entries.map(([key, value]) => (
          <li key={key}>
            <span className="text-gray-500">{SLOT_LABELS[key] || key}:</span>{' '}
            <span className="font-medium text-gray-900">{value}</span>
          </li>
        ))}
      </ul>
    )
  }

  return (
    <div className="flex flex-wrap gap-1.5 mb-3">
      {entries.map(([key, value]) => (
        <span
          key={key}
          className="text-xs bg-violet-50 text-violet-700 border border-violet-100 rounded-md px-2 py-0.5"
        >
          {SLOT_LABELS[key] || key}: {value}
        </span>
      ))}
    </div>
  )
}

export default function App() {
  const [tab, setTab] = useState('chat')
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [history, setHistory] = useState([])
  const [bgJob, setBgJob] = useState(null)
  const [sessionId, setSessionId] = useState(null)
  const [slotInput, setSlotInput] = useState('')
  const [cascadeSelections, setCascadeSelections] = useState({
    coverage: '',
    population: '',
    line: '',
  })
  const downloadedJobRef = useRef(null)

  const awaitingInput = result?.status === 'needs_input' || result?.status === 'error'
  const awaitingCascade = result?.status === 'needs_cascade_input'
  const inConversation = Boolean(sessionId) && (awaitingInput || awaitingCascade)

  useEffect(() => {
    if (!bgJob?.id || bgJob.status !== 'running') return

    let cancelled = false

    async function poll() {
      try {
        const res = await fetch(`/api/eval/jobs/${bgJob.id}`)
        const data = await res.json()
        if (!res.ok) throw new Error(parseErrorDetail(data, 'Failed to poll eval job'))
        if (cancelled) return
        setBgJob(data)

        if (data.status !== 'running' && downloadedJobRef.current !== data.id) {
          downloadedJobRef.current = data.id
          if (data.results?.length) {
            await downloadJobResults(data.id)
          }
        }
      } catch (err) {
        if (!cancelled) setError(formatAwsError(err.message))
      }
    }

    poll()
    const interval = setInterval(poll, 2000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [bgJob?.id, bgJob?.status])

  async function startBackgroundJob({ csvText }) {
    const healthRes = await fetch('/api/health/bedrock')
    const healthData = await healthRes.json().catch(() => ({}))
    if (!healthRes.ok) {
      throw new Error(parseErrorDetail(healthData, 'Bedrock is unavailable. Run `aws sso login` and restart the server.'))
    }

    const res = await fetch('/api/eval/jobs/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        csv_text: csvText,
        default_retrieval_method: RETRIEVAL_METHOD,
      }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(parseErrorDetail(data, 'Failed to start background eval'))

    downloadedJobRef.current = null
    setBgJob({
      id: data.job_id,
      status: 'running',
      progress: 0,
      total: data.total,
      passed: 0,
      pass_rate: 0,
      results: [],
    })
  }

  async function stopBackgroundJob() {
    if (!bgJob?.id) return
    await fetch(`/api/eval/jobs/${bgJob.id}/stop`, { method: 'POST' })
    const res = await fetch(`/api/eval/jobs/${bgJob.id}`)
    const data = await res.json()
    setBgJob(data)
    if (data.results?.length && downloadedJobRef.current !== data.id) {
      downloadedJobRef.current = data.id
      await downloadJobResults(data.id)
    }
  }

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
      setCascadeSelections({ coverage: '', population: '', line: '' })
      setHistory(prev => [{
        question: originalQuestion || data.question,
        method: data.retrieval_method || RETRIEVAL_METHOD,
        result: data,
      }, ...prev])
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
    setCascadeSelections({ coverage: '', population: '', line: '' })
    const asked = question.trim()
    try {
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: asked, retrieval_method: RETRIEVAL_METHOD }),
      })
      const data = await res.json()
      if (!res.ok) {
        throw new Error(parseErrorDetail(data, 'Something went wrong'))
      }
      applyAskResponse(data, asked)
      setQuestion('')
    } catch (err) {
      setError(formatAwsError(err.message))
    } finally {
      setLoading(false)
    }
  }

  async function sendSessionAction(action, value = null) {
    if (!sessionId) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: result?.question || '',
          retrieval_method: RETRIEVAL_METHOD,
          session_id: sessionId,
          action,
          value,
        }),
      })
      const data = await res.json()
      if (!res.ok) {
        throw new Error(parseErrorDetail(data, 'Something went wrong'))
      }
      setSlotInput('')
      applyAskResponse(data, result?.question)
    } catch (err) {
      setError(formatAwsError(err.message))
    } finally {
      setLoading(false)
    }
  }

  async function submitSlotValue() {
    if (!slotInput.trim()) return
    await sendSessionAction('provide', slotInput.trim())
  }

  async function refreshCascadeOptions(nextSelections) {
    setCascadeSelections(nextSelections)
    await sendSessionAction('cascade_options', nextSelections)
  }

  async function onCascadeChange(field, value) {
    let next = { ...cascadeSelections, [field]: value }
    if (field === 'coverage') {
      next = { coverage: value, population: '', line: '' }
    } else if (field === 'population') {
      next = { ...next, line: '' }
    }
    await refreshCascadeOptions(next)
  }

  async function submitCascade() {
    await sendSessionAction('submit_cascade', cascadeSelections)
  }

  function cascadeReady() {
    const pending = result?.cascade?.pending_slots || []
    if (pending.includes('coverage') && !cascadeSelections.coverage) return false
    if (pending.includes('population') && !cascadeSelections.population) return false
    if (pending.includes('line') && !cascadeSelections.line) return false
    return pending.length > 0 || Boolean(cascadeSelections.coverage)
  }

  async function submitFeedback(id, rating) {
    await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, rating }),
    })
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-4xl mx-auto px-4 py-12">
        <div className="mb-10">
          <h1 className="text-xl font-semibold text-gray-900">Pulse AI Chatbot</h1>
          <p className="text-sm text-gray-500 mt-2">
            Operating with the data permissions of <span className="font-medium text-gray-700">orca-payer-demo</span>
            {' '}— answers are limited to payers and products available to that account.
          </p>
        </div>

        {bgJob?.status === 'running' && (
          <div className="bg-violet-50 border border-violet-200 text-violet-800 text-sm rounded-xl p-3 mb-4 flex items-center justify-between">
            <span>
              Batch eval running in background: {bgJob.progress}/{bgJob.total} complete
            </span>
            <button
              onClick={() => setTab('batch')}
              className="text-xs px-2 py-1 rounded border border-violet-200 hover:bg-violet-100"
            >
              View
            </button>
          </div>
        )}

        <div className="flex gap-1 mb-4 border-b border-gray-200">
          {TABS.map(item => (
            <button
              key={item.id}
              onClick={() => setTab(item.id)}
              className={`px-4 py-2 text-sm transition-colors border-b-2 -mb-px ${
                tab === item.id
                  ? 'border-violet-600 text-violet-600 font-medium'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        {tab === 'batch' ? (
          <BatchEval
            bgJob={bgJob}
            onStartJob={startBackgroundJob}
            onStopJob={stopBackgroundJob}
          />
        ) : (
          <>
            <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3">
              <textarea
                rows={3}
                placeholder="Ask a payer question — e.g. What is Calquence's QoA for 1L CLL under commercial plans?"
                className="w-full text-sm text-gray-800 placeholder-gray-400 resize-none outline-none disabled:opacity-50"
                value={question}
                onChange={e => setQuestion(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask() } }}
                disabled={inConversation || loading}
              />
              <div className="flex items-center justify-end mt-3 pt-3 border-t border-gray-100">
                <button
                  onClick={ask}
                  disabled={loading || !question.trim() || inConversation}
                  className="text-sm bg-violet-600 hover:bg-violet-700 disabled:opacity-40 disabled:cursor-not-allowed text-white px-4 py-1.5 rounded-lg transition-colors"
                >
                  {loading && !inConversation ? 'Thinking...' : 'Ask'}
                </button>
              </div>
            </div>

            {error && (
              <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl p-4 mb-3">
                {error}
              </div>
            )}

            {result && (
              <div className="bg-white border border-gray-200 rounded-xl p-4 mb-6">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-xs font-medium text-violet-600 uppercase tracking-wide">
                    {result.status === 'complete'
                      ? 'Answer'
                      : awaitingCascade
                        ? 'Additional details'
                        : 'Clarify slots'}
                  </span>
                  <span className="text-xs text-gray-400">
                    {result.retrieval_method}
                    {result.status === 'complete' ? ` · ${result.records_retrieved} records` : ''}
                  </span>
                </div>

                {result.question && (
                  <p className="text-sm text-gray-500 mb-3 pb-3 border-b border-gray-100">
                    <span className="text-xs font-medium text-gray-400 uppercase tracking-wide mr-2">Question</span>
                    {result.question}
                  </p>
                )}

                {result.status === 'complete' ? (
                  <>
                    <ConfirmedSlots slots={result.confirmed_slots} />
                    <p className="text-sm text-gray-800 leading-relaxed whitespace-pre-wrap">{result.answer}</p>
                    {result.citations?.length > 0 && (
                      <div className="mt-4 pt-3 border-t border-gray-100">
                        <p className="text-xs text-gray-400 mb-2">Sources</p>
                        <div className="flex flex-col gap-1">
                          {result.citations.map((c, i) => (
                            <div key={i} className="text-xs text-gray-500 bg-gray-50 rounded px-2 py-1 font-mono">{c}</div>
                          ))}
                        </div>
                      </div>
                    )}
                    {result.id && (
                      <div className="mt-4 pt-3 border-t border-gray-100 flex items-center gap-3">
                        <span className="text-xs text-gray-400">Would you send this to a client?</span>
                        <button onClick={() => submitFeedback(result.id, 'yes')} className="text-xs px-3 py-1 rounded-md border border-gray-200 hover:bg-green-50 hover:border-green-300 hover:text-green-700 transition-colors">👍 Yes</button>
                        <button onClick={() => submitFeedback(result.id, 'no')} className="text-xs px-3 py-1 rounded-md border border-gray-200 hover:bg-red-50 hover:border-red-300 hover:text-red-700 transition-colors">👎 No</button>
                      </div>
                    )}
                  </>
                ) : awaitingCascade ? (
                  <>
                    <p className="text-sm font-medium text-gray-900 mb-2">Here's what I have confirmed</p>
                    <ConfirmedSlots slots={result.confirmed_slots} asList />
                    <p className="text-sm text-gray-600 mb-4 pt-3 border-t border-gray-100">
                      I would need some additional information from you to give you an accurate response
                    </p>
                    <div className="space-y-3">
                      {(result.cascade?.pending_slots || []).includes('coverage') && (
                        <label className="block">
                          <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">Coverage</span>
                          <select
                            value={cascadeSelections.coverage}
                            onChange={e => onCascadeChange('coverage', e.target.value)}
                            disabled={loading}
                            className="mt-1 w-full text-sm border border-gray-200 rounded-lg px-3 py-2 outline-none bg-white focus:border-violet-300 disabled:opacity-50"
                          >
                            <option value="">Select coverage…</option>
                            {(result.cascade?.options?.coverage || []).map(option => (
                              <option key={option} value={option}>{option}</option>
                            ))}
                          </select>
                        </label>
                      )}
                      {(result.cascade?.pending_slots || []).includes('population') && (
                        <label className="block">
                          <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">Subtype</span>
                          <select
                            value={cascadeSelections.population}
                            onChange={e => onCascadeChange('population', e.target.value)}
                            disabled={loading || !cascadeSelections.coverage}
                            className="mt-1 w-full text-sm border border-gray-200 rounded-lg px-3 py-2 outline-none bg-white focus:border-violet-300 disabled:opacity-50"
                          >
                            <option value="">
                              {cascadeSelections.coverage ? 'Select subtype…' : 'Select coverage first…'}
                            </option>
                            {(result.cascade?.options?.population || []).map(option => (
                              <option key={option} value={option}>{option}</option>
                            ))}
                          </select>
                        </label>
                      )}
                      {(result.cascade?.pending_slots || []).includes('line') && (
                        <label className="block">
                          <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">Line</span>
                          <select
                            value={cascadeSelections.line}
                            onChange={e => onCascadeChange('line', e.target.value)}
                            disabled={
                              loading
                              || !cascadeSelections.coverage
                              || (
                                (result.cascade?.pending_slots || []).includes('population')
                                && !cascadeSelections.population
                              )
                            }
                            className="mt-1 w-full text-sm border border-gray-200 rounded-lg px-3 py-2 outline-none bg-white focus:border-violet-300 disabled:opacity-50"
                          >
                            <option value="">
                              {!cascadeSelections.coverage
                                ? 'Select coverage first…'
                                : (result.cascade?.pending_slots || []).includes('population') && !cascadeSelections.population
                                  ? 'Select subtype first…'
                                  : 'Select line of therapy…'}
                            </option>
                            {(result.cascade?.options?.line || []).map(option => (
                              <option key={option} value={option}>{option}</option>
                            ))}
                          </select>
                        </label>
                      )}
                    </div>
                    <div className="mt-4 flex items-center gap-2">
                      <button
                        onClick={submitCascade}
                        disabled={loading || !cascadeReady()}
                        className="text-sm bg-violet-600 hover:bg-violet-700 disabled:opacity-40 disabled:cursor-not-allowed text-white px-4 py-1.5 rounded-lg transition-colors"
                      >
                        {loading ? 'Looking up…' : 'Get answer'}
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    {Object.keys(result.confirmed_slots || {}).length > 0 && (
                      <>
                        <p className="text-sm font-medium text-gray-900 mb-2">Here's what I have confirmed</p>
                        <ConfirmedSlots slots={result.confirmed_slots} asList />
                      </>
                    )}
                    <p className="text-sm text-gray-800 leading-relaxed mb-4 whitespace-pre-wrap">
                      {result.message || result.answer}
                    </p>

                    {awaitingInput && result.status !== 'error' && (
                      <div className="pt-3 border-t border-gray-100">
                        <p className="text-xs text-gray-400 mb-2">
                          Select {SLOT_LABELS[result.current_slot] || result.current_slot || 'value'}
                        </p>
                        {result.candidates?.length > 0 ? (
                          <div className="space-y-3">
                            <select
                              value={slotInput}
                              onChange={e => setSlotInput(e.target.value)}
                              disabled={loading}
                              className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 outline-none bg-white focus:border-violet-300 disabled:opacity-50"
                            >
                              <option value="">
                                Select {SLOT_LABELS[result.current_slot] || 'value'}…
                              </option>
                              {result.candidates.map(candidate => (
                                <option key={candidate} value={candidate}>
                                  {candidate}
                                </option>
                              ))}
                            </select>
                            <div className="flex items-center gap-2">
                              <button
                                onClick={submitSlotValue}
                                disabled={loading || !slotInput.trim()}
                                className="text-sm bg-violet-600 hover:bg-violet-700 disabled:opacity-40 disabled:cursor-not-allowed text-white px-4 py-1.5 rounded-lg transition-colors"
                              >
                                {loading ? 'Looking up…' : 'Confirm'}
                              </button>
                              {result.allow_skip && (
                                <button
                                  type="button"
                                  disabled={loading}
                                  onClick={() => sendSessionAction('skip', null)}
                                  className="text-xs text-gray-500 hover:text-gray-700 underline disabled:opacity-40"
                                >
                                  Skip this field
                                </button>
                              )}
                            </div>
                          </div>
                        ) : (
                          <div className="flex gap-2">
                            <input
                              type="text"
                              value={slotInput}
                              onChange={e => setSlotInput(e.target.value)}
                              onKeyDown={e => {
                                if (e.key === 'Enter') {
                                  e.preventDefault()
                                  submitSlotValue()
                                }
                              }}
                              placeholder="Type value…"
                              disabled={loading}
                              className="flex-1 text-sm border border-gray-200 rounded-lg px-3 py-1.5 outline-none focus:border-violet-300"
                            />
                            <button
                              onClick={submitSlotValue}
                              disabled={loading || !slotInput.trim()}
                              className="text-sm bg-gray-900 hover:bg-gray-800 disabled:opacity-40 text-white px-3 py-1.5 rounded-lg transition-colors"
                            >
                              Submit
                            </button>
                          </div>
                        )}
                      </div>
                    )}

                    {result.status === 'error' && (
                      <button
                        onClick={() => {
                          setSessionId(null)
                          setResult(null)
                          setSlotInput('')
                          setCascadeSelections({ coverage: '', population: '', line: '' })
                        }}
                        className="mt-3 text-xs text-violet-600 hover:underline"
                      >
                        Start over
                      </button>
                    )}
                  </>
                )}
              </div>
            )}

            {history.length > 0 && (
              <div>
                <p className="text-xs text-gray-400 uppercase tracking-wide mb-3">History</p>
                <div className="flex flex-col gap-2">
                  {history.map((h, i) => (
                    <button key={i} onClick={() => setResult({ ...h.result, question: h.question })} className="text-left bg-white border border-gray-200 rounded-lg px-3 py-2.5 hover:border-violet-200 transition-colors">
                      <p className="text-sm text-gray-700 truncate">{h.question}</p>
                      <p className="text-xs text-gray-400 mt-0.5">{h.method}</p>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
