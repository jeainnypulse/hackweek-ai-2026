import ConfirmedSlots from './ConfirmedSlots'

export default function AnswerView({ result, onFeedback }) {
  return (
    <>
      <ConfirmedSlots slots={result.confirmed_slots} />
      <p className="text-sm text-gray-800 leading-relaxed whitespace-pre-wrap">
        {result.answer}
      </p>

      {result.citations?.length > 0 && (
        <div className="mt-4 pt-3 border-t border-gray-100">
          <p className="text-xs text-gray-400 mb-2">Sources</p>
          <div className="flex flex-col gap-1">
            {result.citations.map((citation, index) => (
              <div
                key={index}
                className="text-xs text-gray-500 bg-gray-50 rounded px-2 py-1 font-mono"
              >
                {citation}
              </div>
            ))}
          </div>
        </div>
      )}

      {result.id && (
        <div className="mt-4 pt-3 border-t border-gray-100 flex items-center gap-3">
          <span className="text-xs text-gray-400">
            Would you send this to a client?
          </span>
          <button
            onClick={() => onFeedback(result.id, 'yes')}
            className="text-xs px-3 py-1 rounded-md border border-gray-200 hover:bg-green-50 hover:border-green-300 hover:text-green-700 transition-colors"
          >
            👍 Yes
          </button>
          <button
            onClick={() => onFeedback(result.id, 'no')}
            className="text-xs px-3 py-1 rounded-md border border-gray-200 hover:bg-red-50 hover:border-red-300 hover:text-red-700 transition-colors"
          >
            👎 No
          </button>
        </div>
      )}
    </>
  )
}
