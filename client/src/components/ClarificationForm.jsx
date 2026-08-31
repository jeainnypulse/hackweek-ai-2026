import { SLOT_LABELS } from '../constants/slots'
import ConfirmedSlots from './ConfirmedSlots'

export default function ClarificationForm({
  result,
  slotInput,
  loading,
  onSlotInputChange,
  onSubmit,
  onStartOver,
}) {
  const hasConfirmedSlots =
    Object.keys(result.confirmed_slots || {}).length > 0
  const slotLabel =
    SLOT_LABELS[result.current_slot] || result.current_slot || 'value'

  return (
    <>
      {hasConfirmedSlots && (
        <>
          <p className="text-sm font-medium text-gray-900 mb-2">
            Here's what I have confirmed
          </p>
          <ConfirmedSlots slots={result.confirmed_slots} asList />
        </>
      )}

      <p className="text-sm text-gray-800 leading-relaxed mb-4 whitespace-pre-wrap">
        {result.message || result.answer}
      </p>

      {result.status === 'needs_input' && (
        <div className="pt-3 border-t border-gray-100">
          <p className="text-xs text-gray-400 mb-2">Select {slotLabel}</p>

          {result.candidates?.length > 0 ? (
            <div className="space-y-3">
              <select
                value={slotInput}
                onChange={event => onSlotInputChange(event.target.value)}
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
                  onClick={onSubmit}
                  disabled={loading || !slotInput.trim()}
                  className="text-sm bg-violet-600 hover:bg-violet-700 disabled:opacity-40 disabled:cursor-not-allowed text-white px-4 py-1.5 rounded-lg transition-colors"
                >
                  {loading ? 'Looking up…' : 'Confirm'}
                </button>
              </div>
            </div>
          ) : (
            <div className="flex gap-2">
              <input
                type="text"
                value={slotInput}
                onChange={event => onSlotInputChange(event.target.value)}
                onKeyDown={event => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    onSubmit()
                  }
                }}
                placeholder="Type value…"
                disabled={loading}
                className="flex-1 text-sm border border-gray-200 rounded-lg px-3 py-1.5 outline-none focus:border-violet-300"
              />
              <button
                onClick={onSubmit}
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
          onClick={onStartOver}
          className="mt-3 text-xs text-violet-600 hover:underline"
        >
          Start over
        </button>
      )}
    </>
  )
}
