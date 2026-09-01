import { Typography } from '@pulse-analytics/pulse-design-system'

export default function ChatHistory({ history, onSelect }) {
  if (!history.length) return null

  return (
    <div>
      <Typography
        variant="Overline50.Medium"
        className="text-gray-400 mb-3"
      >
        History
      </Typography>
      <div className="flex flex-col gap-2">
        {history.map((item, index) => (
          <button
            key={index}
            onClick={() => onSelect(item)}
            className="text-left bg-white border border-gray-200 rounded-lg px-3 py-2.5 hover:border-violet-200 transition-colors"
          >
            <Typography
              variant="Body100.Regular"
              className="text-gray-700 truncate"
            >
              {item.question}
            </Typography>
          </button>
        ))}
      </div>
    </div>
  )
}
