import { useEffect, useState } from 'react'
import { parseCsv } from './evalUtils'

const RETRIEVAL_METHODS = [
  { value: 'exact_match', label: 'Exact Match' },
  { value: 'vector_search', label: 'Vector Search' },
  { value: 'combined', label: 'Combined (Mongo + Docs)' },
]

export default function BatchEval({ bgJob, onStartJob, onStopJob }) {
  const [method, setMethod] = useState('exact_match')
  const [csvText, setCsvText] = useState('')
  const [fileName, setFileName] = useState('qa_pairs.csv')
  const [error, setError] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [starting, setStarting] = useState(false)

  const running = bgJob?.status === 'running'
  const results = bgJob?.results || []
  const progress = bgJob?.progress || 0
  const questionCount = parseCsv(csvText).length

  useEffect(() => {
    fetch('/qa_pairs.csv')
      .then(res => res.text())
      .then(text => {
        setCsvText(text)
        setFileName('qa_pairs.csv')
      })
      .catch(() => {
        fetch('/eval_questions.csv')
          .then(res => res.text())
          .then(text => {
            setCsvText(text)
            setFileName('eval_questions.csv')
          })
          .catch(() => {})
      })
  }, [])

  function handleFileUpload(event) {
    const file = event.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      setCsvText(String(reader.result || ''))
      setFileName(file.name)
      setError(null)
    }
    reader.readAsText(file)
  }

  async function runInBackground() {
    if (questionCount === 0) {
      setError('No questions found in CSV. Expected columns: id, type, question, expected_slots, expected_access, citation')
      return
    }

    setStarting(true)
    setError(null)

    try {
      await onStartJob({ csvText, method })
    } catch (err) {
      setError(err.message)
    } finally {
      setStarting(false)
    }
  }

  return (
    <div>
      <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3">
        <p className="text-sm text-gray-700 mb-3">
          Run all questions on the server in the background. Results auto-download as CSV when finished — you can switch tabs while it runs.
        </p>

        <div className="flex flex-wrap items-center gap-3 mb-3">
          <label className="text-xs px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 cursor-pointer hover:bg-gray-50">
            Upload CSV
            <input type="file" accept=".csv" className="hidden" onChange={handleFileUpload} />
          </label>
          <span className="text-xs text-gray-400">{fileName} · {questionCount} questions</span>
          <div className="flex items-center gap-2 ml-auto">
            <span className="text-xs text-gray-400">Default retrieval:</span>
            <select
              value={method}
              onChange={e => setMethod(e.target.value)}
              disabled={running || starting}
              className="text-xs border border-gray-200 rounded-md px-2 py-1 text-gray-600 outline-none bg-white"
            >
              {RETRIEVAL_METHODS.map(m => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </div>
        </div>

        <p className="text-xs text-gray-400 mb-3">
          CSV columns: id, type, question, expected_slots, expected_access, citation
        </p>

        <div className="flex items-center gap-3">
          <button
            onClick={runInBackground}
            disabled={running || starting || questionCount === 0}
            className="text-sm bg-violet-600 hover:bg-violet-700 disabled:opacity-40 disabled:cursor-not-allowed text-white px-4 py-1.5 rounded-lg transition-colors"
          >
            {running
              ? `Running ${progress}/${bgJob?.total || questionCount}...`
              : starting
                ? 'Starting...'
                : `Run ${questionCount} in Background`}
          </button>
          {running && (
            <button
              onClick={onStopJob}
              className="text-sm px-4 py-1.5 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 transition-colors"
            >
              Stop
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl p-4 mb-3">
          {error}
        </div>
      )}

      {bgJob && bgJob.status !== 'running' && results.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3">
          <div className="flex items-center gap-6 text-sm">
            <span><strong>{bgJob.passed}</strong> / {bgJob.total} passed (access + slots)</span>
            <span className="text-gray-500">Pass rate: {((bgJob.pass_rate || 0) * 100).toFixed(0)}%</span>
            {bgJob.status === 'stopped' && <span className="text-amber-600">Stopped early</span>}
            {bgJob.status === 'completed' && <span className="text-green-600">Complete — CSV downloaded</span>}
          </div>
        </div>
      )}

      {results.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <div className="grid grid-cols-[48px_120px_1fr_80px_80px_80px] gap-3 px-4 py-2 border-b border-gray-100 text-xs font-medium text-gray-400 uppercase tracking-wide">
            <span>#</span>
            <span>Type</span>
            <span>Question</span>
            <span>Overall</span>
            <span>Access</span>
            <span>Slots</span>
          </div>
          <div className="divide-y divide-gray-100">
            {results.map(row => {
              const isOpen = expandedId === row.id
              const matched = row.comparison?.match
              const accessMatch = row.comparison?.access?.match
              const slotsMatch = row.comparison?.slots?.match
              const statusClass = (value) => {
                if (row.error) return 'text-red-600'
                if (value === true) return 'text-green-600'
                if (value === false) return 'text-amber-600'
                return 'text-gray-400'
              }
              const statusLabel = (value) => {
                if (row.error) return 'Error'
                if (value === true) return 'Pass'
                if (value === false) return 'Fail'
                return '—'
              }
              return (
                <div key={row.id}>
                  <button
                    onClick={() => setExpandedId(isOpen ? null : row.id)}
                    className="w-full grid grid-cols-[48px_120px_1fr_80px_80px_80px] gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors"
                  >
                    <span className="text-xs text-gray-400">{row.id}</span>
                    <span className="text-xs text-gray-500 truncate">{row.type || '—'}</span>
                    <span className="text-sm text-gray-700 truncate">{row.question}</span>
                    <span className={`text-xs font-medium ${statusClass(matched)}`}>
                      {statusLabel(matched)}
                    </span>
                    <span className={`text-xs font-medium ${statusClass(accessMatch)}`}>
                      {statusLabel(accessMatch)}
                    </span>
                    <span className={`text-xs font-medium ${statusClass(slotsMatch)}`}>
                      {statusLabel(slotsMatch)}
                    </span>
                  </button>
                  {isOpen && (
                    <div className="px-4 pb-4 space-y-3 bg-gray-50">
                      {row.citation && (
                        <div>
                          <p className="text-xs text-gray-400 mb-1">Citation</p>
                          <p className="text-xs text-gray-500 font-mono">{row.citation}</p>
                        </div>
                      )}
                      {row.expected_slots && (
                        <div>
                          <p className="text-xs text-gray-400 mb-1">Expected Slots</p>
                          <p className="text-xs text-gray-500 font-mono">{JSON.stringify(row.expected_slots)}</p>
                        </div>
                      )}
                      {row.comparison?.actual_slots && (
                        <div>
                          <p className="text-xs text-gray-400 mb-1">Extracted Slots</p>
                          <p className="text-xs text-gray-500 font-mono">{JSON.stringify(row.comparison.actual_slots)}</p>
                        </div>
                      )}
                      <div>
                        <p className="text-xs text-gray-400 mb-1">Expected Access</p>
                        <p className="text-sm text-gray-700">{row.expected_access || '—'}</p>
                      </div>
                      <div>
                        <p className="text-xs text-gray-400 mb-1">Bedrock Answer</p>
                        <p className="text-sm text-gray-800">{row.actual_answer || '—'}</p>
                      </div>
                      <div className="flex gap-4 text-xs text-gray-500">
                        <span>Records: {row.records_retrieved}</span>
                        {row.comparison?.access?.score != null && (
                          <span>Access score: {(row.comparison.access.score * 100).toFixed(0)}%</span>
                        )}
                        {row.comparison?.slots?.score != null && (
                          <span>Slots score: {(row.comparison.slots.score * 100).toFixed(0)}%</span>
                        )}
                      </div>
                      {row.error && (
                        <div>
                          <p className="text-xs text-red-500 mb-1">Error</p>
                          <p className="text-sm text-red-700">{row.error}</p>
                        </div>
                      )}
                      {row.comparison?.access?.matched_terms?.length > 0 && (
                        <p className="text-xs text-gray-500">
                          Access matched terms: {row.comparison.access.matched_terms.join(', ')}
                        </p>
                      )}
                      {row.comparison?.slots?.mismatched_fields?.length > 0 && (
                        <div>
                          <p className="text-xs text-gray-400 mb-1">Slot mismatches</p>
                          <ul className="text-xs text-amber-700 space-y-1">
                            {row.comparison.slots.mismatched_fields.map(item => (
                              <li key={item.field}>
                                {item.field}: expected "{item.expected}", got "{item.actual ?? 'null'}"
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
