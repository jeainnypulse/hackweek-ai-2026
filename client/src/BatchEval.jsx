import { useEffect, useState } from 'react'
import { parseCsv } from './evalUtils'

const EVAL_SETS = [
  {
    id: 'linker',
    file: 'pulse_eval_orca_tregzi.csv',
    label: 'Linker (slots)',
    blurb:
      'Scores linked identity slots only (organization, regimen, indication, book). Does not run retrieve or synthesis.',
    columns: 'id, type, question, expected_slots, expected_access, citation',
  },
  {
    id: 'orchestration',
    file: 'pulse_eval_orchestration.csv',
    label: 'Orchestration (intent)',
    blurb:
      'Scores the access vs lives vs out-of-scope router and first-turn behavior (complete / cascade / clarify / refuse). Runs classify + extract/link/retrieve for in-scope rows.',
    columns:
      'id, type, question, expected_intent, expected_behavior, expected_slots, expected_lives',
  },
]

export default function BatchEval({ bgJob, onStartJob, onStopJob }) {
  const [evalKind, setEvalKind] = useState('orchestration')
  const [csvText, setCsvText] = useState('')
  const [fileName, setFileName] = useState('pulse_eval_orchestration.csv')
  const [error, setError] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [starting, setStarting] = useState(false)

  const activeSet = EVAL_SETS.find(item => item.id === evalKind) || EVAL_SETS[0]
  const running = bgJob?.status === 'running'
  const results = bgJob?.results || []
  const progress = bgJob?.progress || 0
  const questionCount = parseCsv(csvText).length

  useEffect(() => {
    let cancelled = false
    fetch(`/${activeSet.file}`)
      .then(res => {
        if (!res.ok) throw new Error('missing eval csv')
        return res.text()
      })
      .then(text => {
        if (cancelled) return
        setCsvText(text)
        setFileName(activeSet.file)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [activeSet.file])

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
      setError(`No questions found in CSV. Expected columns: ${activeSet.columns}`)
      return
    }

    setStarting(true)
    setError(null)

    try {
      await onStartJob({ csvText, evalKind })
    } catch (err) {
      setError(err.message)
    } finally {
      setStarting(false)
    }
  }

  const orchestrationResults = Boolean(
    bgJob?.eval_kind === 'orchestration' || results[0]?.comparison?.expected_intent
  )

  return (
    <div>
      <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3">
        <p className="text-sm text-gray-700 mb-3">
          Run all questions on the server in the background. Results auto-download as CSV when finished — you can switch tabs while it runs.
        </p>

        <div className="flex flex-wrap gap-2 mb-3">
          {EVAL_SETS.map(item => (
            <button
              key={item.id}
              type="button"
              onClick={() => setEvalKind(item.id)}
              disabled={running}
              className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
                evalKind === item.id
                  ? 'border-violet-300 bg-violet-50 text-violet-700'
                  : 'border-gray-200 text-gray-600 hover:bg-gray-50'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        <p className="text-xs text-violet-700 bg-violet-50 border border-violet-100 rounded-lg px-3 py-2 mb-3">
          {activeSet.blurb}
        </p>

        <div className="flex flex-wrap items-center gap-3 mb-3">
          <label className="text-xs px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 cursor-pointer hover:bg-gray-50">
            Upload CSV
            <input type="file" accept=".csv" className="hidden" onChange={handleFileUpload} />
          </label>
          <span className="text-xs text-gray-400">{fileName} · {questionCount} questions</span>
        </div>

        <p className="text-xs text-gray-400 mb-3">
          CSV columns: {activeSet.columns}.
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
            <span>
              <strong>{bgJob.passed}</strong> / {bgJob.total} passed
              {' '}({orchestrationResults ? 'intent + first-turn behavior' : 'linked identity slots'})
            </span>
            <span className="text-gray-500">Pass rate: {((bgJob.pass_rate || 0) * 100).toFixed(0)}%</span>
            {bgJob.status === 'stopped' && <span className="text-amber-600">Stopped early</span>}
            {bgJob.status === 'completed' && <span className="text-green-600">Complete — CSV downloaded</span>}
          </div>
        </div>
      )}

      {results.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <div className="grid grid-cols-[48px_120px_1fr_80px_90px_90px] gap-3 px-4 py-2 border-b border-gray-100 text-xs font-medium text-gray-400 uppercase tracking-wide">
            <span>#</span>
            <span>Type</span>
            <span>Question</span>
            <span>Overall</span>
            <span>{orchestrationResults ? 'Intent' : 'Slots'}</span>
            <span>{orchestrationResults ? 'Behavior' : 'Clarify'}</span>
          </div>
          <div className="divide-y divide-gray-100">
            {results.map(row => {
              const isOpen = expandedId === row.id
              const matched = row.comparison?.match
              const slotsMatch = orchestrationResults
                ? row.comparison?.intent_match
                : row.comparison?.slots?.match
              const clarifyNeeded = row.comparison?.clarify_needed
              const behaviorOk = row.comparison?.behavior_match
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
              const clarifyLabel = row.error
                ? 'Error'
                : clarifyNeeded === true
                  ? 'Yes'
                  : clarifyNeeded === false
                    ? 'No'
                    : '—'
              const clarifyClass = row.error
                ? 'text-red-600'
                : clarifyNeeded
                  ? 'text-amber-600'
                  : 'text-gray-400'
              return (
                <div key={row.id}>
                  <button
                    onClick={() => setExpandedId(isOpen ? null : row.id)}
                    className="w-full grid grid-cols-[48px_120px_1fr_80px_90px_90px] gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors"
                  >
                    <span className="text-xs text-gray-400">{row.id}</span>
                    <span className="text-xs text-gray-500 truncate">{row.type || '—'}</span>
                    <span className="text-sm text-gray-700 truncate">{row.question}</span>
                    <span className={`text-xs font-medium ${statusClass(matched)}`}>
                      {statusLabel(matched)}
                    </span>
                    <span className={`text-xs font-medium ${statusClass(slotsMatch)}`}>
                      {orchestrationResults
                        ? `${row.comparison?.expected_intent || '—'} → ${row.comparison?.actual_intent || '—'}`
                        : statusLabel(slotsMatch)}
                    </span>
                    <span className={`text-xs font-medium ${
                      orchestrationResults ? statusClass(behaviorOk) : clarifyClass
                    }`}>
                      {orchestrationResults
                        ? (row.comparison?.observed_behavior || '—')
                        : clarifyLabel}
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
                          <p className="text-xs text-gray-400 mb-1">Linked Slots</p>
                          <p className="text-xs text-gray-500 font-mono">{JSON.stringify(row.comparison.actual_slots)}</p>
                        </div>
                      )}
                      {row.comparison?.link_info && Object.keys(row.comparison.link_info).length > 0 && (
                        <div>
                          <p className="text-xs text-gray-400 mb-1">Link info</p>
                          <p className="text-xs text-gray-500 font-mono whitespace-pre-wrap">
                            {JSON.stringify(row.comparison.link_info, null, 2)}
                          </p>
                        </div>
                      )}
                      {orchestrationResults && (
                        <div className="flex gap-4 text-xs text-gray-500">
                          <span>Intent: {row.comparison?.expected_intent} → {row.comparison?.actual_intent}</span>
                          <span>Behavior: {row.comparison?.expected_behavior || 'any'} → {row.comparison?.observed_behavior}</span>
                          {row.comparison?.expected_lives != null && (
                            <span>Lives: {row.comparison.expected_lives} → {row.comparison.actual_lives ?? '—'}</span>
                          )}
                        </div>
                      )}
                      {row.error && (
                        <div>
                          <p className="text-xs text-red-500 mb-1">Error</p>
                          <p className="text-sm text-red-700">{row.error}</p>
                        </div>
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
