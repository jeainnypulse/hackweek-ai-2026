import { useEffect, useRef, useState } from 'react'
import BatchEval from './BatchEval'
import { downloadJobResults } from './evalUtils'
import { RETRIEVAL_METHODS } from './retrievalMethods'

const TABS = [
  { id: 'chat', label: 'Chat' },
  { id: 'batch', label: 'Batch Eval' },
]

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

export default function App() {
  const [tab, setTab] = useState('chat')
  const [question, setQuestion] = useState('')
  const [method, setMethod] = useState('exact_match')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [history, setHistory] = useState([])
  const [bgJob, setBgJob] = useState(null)
  const downloadedJobRef = useRef(null)

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

  async function startBackgroundJob({ csvText, method: retrievalMethod }) {
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
        default_retrieval_method: retrievalMethod,
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

  async function ask() {
    if (!question.trim()) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, retrieval_method: method }),
      })
      const data = await res.json()
      if (!res.ok) {
        throw new Error(parseErrorDetail(data, 'Something went wrong'))
      }
      setResult(data)
      setHistory(prev => [{ question, method, result: data }, ...prev])
      setQuestion('')
    } catch (err) {
      setError(formatAwsError(err.message))
    } finally {
      setLoading(false)
    }
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
          <p className="text-sm text-gray-400 mt-0.5">Phase 1 POC — Internal only</p>
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
                className="w-full text-sm text-gray-800 placeholder-gray-400 resize-none outline-none"
                value={question}
                onChange={e => setQuestion(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask() } }}
              />
              <div className="flex items-center justify-between mt-3 pt-3 border-t border-gray-100">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-400">Retrieval:</span>
                  <select
                    value={method}
                    onChange={e => setMethod(e.target.value)}
                    className="text-xs border border-gray-200 rounded-md px-2 py-1 text-gray-600 outline-none bg-white"
                  >
                    {RETRIEVAL_METHODS.map(m => (
                      <option key={m.value} value={m.value}>{m.label}</option>
                    ))}
                  </select>
                </div>
                <button
                  onClick={ask}
                  disabled={loading || !question.trim()}
                  className="text-sm bg-violet-600 hover:bg-violet-700 disabled:opacity-40 disabled:cursor-not-allowed text-white px-4 py-1.5 rounded-lg transition-colors"
                >
                  {loading ? 'Thinking...' : 'Ask'}
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
                  <span className="text-xs font-medium text-violet-600 uppercase tracking-wide">Answer</span>
                  <span className="text-xs text-gray-400">{result.retrieval_method} · {result.records_retrieved} records</span>
                </div>
                {result.question && (
                  <p className="text-sm text-gray-500 mb-3 pb-3 border-b border-gray-100">
                    <span className="text-xs font-medium text-gray-400 uppercase tracking-wide mr-2">Question</span>
                    {result.question}
                  </p>
                )}
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
                <div className="mt-4 pt-3 border-t border-gray-100 flex items-center gap-3">
                  <span className="text-xs text-gray-400">Would you send this to a client?</span>
                  <button onClick={() => submitFeedback(result.id, 'yes')} className="text-xs px-3 py-1 rounded-md border border-gray-200 hover:bg-green-50 hover:border-green-300 hover:text-green-700 transition-colors">👍 Yes</button>
                  <button onClick={() => submitFeedback(result.id, 'no')} className="text-xs px-3 py-1 rounded-md border border-gray-200 hover:bg-red-50 hover:border-red-300 hover:text-red-700 transition-colors">👎 No</button>
                </div>
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
