import { Typography } from '@pulse-analytics/pulse-design-system'

import { SLOT_DISPLAY_ORDER, SLOT_LABELS } from '../constants/slots'

export default function ConfirmedSlots({ slots, asList = false }) {
  const entries = SLOT_DISPLAY_ORDER
    .filter(key => slots?.[key])
    .map(key => [key, slots[key]])

  if (!entries.length) return null

  if (asList) {
    return (
      <Typography
        variant="Body100.Regular"
        className="text-gray-700 space-y-1 mb-3"
      >
        {entries.map(([key, value]) => (
          <Typography variant="Body100.Regular" key={key}>
            <Typography
              variant="Body100.Regular"
              className="text-gray-500"
            >
              {SLOT_LABELS[key] || key}:
            </Typography>{' '}
            <Typography
              variant="Body100.Medium"
              className="text-gray-900"
            >
              {value}
            </Typography>
          </Typography>
        ))}
      </Typography>
    )
  }

  return (
    <div className="flex flex-wrap gap-1.5 mb-3">
      {entries.map(([key, value]) => (
        <Typography
          variant="Body50.Regular"
          key={key}
          className="bg-violet-50 text-violet-700 border border-violet-100 rounded-md px-2 py-0.5"
        >
          {SLOT_LABELS[key] || key}: {value}
        </Typography>
      ))}
    </div>
  )
}
