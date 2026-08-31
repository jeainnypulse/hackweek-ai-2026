import { SLOT_DISPLAY_ORDER, SLOT_LABELS } from '../constants/slots'

export default function ConfirmedSlots({ slots, asList = false }) {
  const entries = SLOT_DISPLAY_ORDER
    .filter(key => slots?.[key])
    .map(key => [key, slots[key]])

  if (!entries.length) return null

  if (asList) {
    return (
      <ul className="text-sm text-gray-700 space-y-1 mb-3">
        {entries.map(([key, value]) => (
          <li key={key}>
            <span className="text-gray-500">
              {SLOT_LABELS[key] || key}:
            </span>{' '}
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
