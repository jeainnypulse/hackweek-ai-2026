export default function HistoryList({ history, onSelect }) {
  if (!history.length) return null

  return (
    <div>
      <p className="text-xs text-gray-400 uppercase tracking-wide mb-3">
        History
      </p>
      <div className="flex flex-col gap-2">
        {history.map((item, index) => (
          <button
            key={index}
            onClick={() => onSelect(item)}
            className="text-left bg-white border border-gray-200 rounded-lg px-3 py-2.5 hover:border-violet-200 transition-colors"
          >
            <p className="text-sm text-gray-700 truncate">{item.question}</p>
          </button>
        ))}
      </div>
    </div>
  )
}
