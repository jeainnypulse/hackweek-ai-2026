import {
  Colors,
  Select,
  Typography,
} from '@pulse-analytics/pulse-design-system'

import { SLOT_LABELS } from '../constants/slots'
import PulseAiIcon from './PulseAiIcon'

function formatPrompt(prompt, slot) {
  return slot === 'regimen'
    ? prompt?.replace(/\bproduct\b/gi, 'regimen')
    : prompt
}

export default function ClarificationForm({
  result,
  turns,
  slotInput,
  loading,
  onSlotInputChange,
  onSubmit,
  onStartOver,
}) {
  const slotLabel =
    SLOT_LABELS[result.current_slot] || result.current_slot || 'value'

  return (
    <div className="pulse-slot-flow">
      {turns.map((turn, index) => (
        <div className="pulse-slot-turn" key={`${turn.slot}-${index}`}>
          <div className="pulse-response-row pulse-slot-prompt">
            <PulseAiIcon className="pulse-response-icon" />
            <Typography variant="Body50.Regular">
              {formatPrompt(turn.prompt, turn.slot)}
            </Typography>
          </div>
          <div
            className="pulse-slot-answer"
            style={{ backgroundColor: Colors.Primary }}
          >
            <Typography variant="Body50.Regular">{turn.value}</Typography>
          </div>
        </div>
      ))}

      {loading && (
        <div
          className="pulse-response-row pulse-slot-current"
          role="status"
          aria-live="polite"
        >
          <PulseAiIcon className="pulse-response-icon" />
          <Typography variant="Body50.Regular">Thinking...</Typography>
          <span className="pulse-loading-spinner" aria-hidden="true" />
        </div>
      )}

      {result.status === 'needs_input' && !loading && (
        <div className="pulse-response-row pulse-slot-current">
          <PulseAiIcon className="pulse-response-icon" />
          <div className="pulse-slot-control">
            <Typography variant="Body50.Regular">
              {formatPrompt(
                result.message || result.answer,
                result.current_slot,
              )}
            </Typography>
            <Select
              options={(result.candidates || []).map(candidate => ({
                label: candidate,
                value: candidate,
              }))}
              value={slotInput}
              onChange={option => {
                const value = option?.value || ''
                onSlotInputChange(value)
                if (value) onSubmit(value)
              }}
              isDisabled={loading}
              isSearchable
              disableUIForSingleOption={false}
              placeholderText={slotLabel}
              dropdownWidth={{ width: '100%', maxWidth: 'none' }}
              selectProps={{ 'aria-label': `Choose ${slotLabel}` }}
            />
          </div>
        </div>
      )}

      {result.status === 'error' && (
        <button
          type="button"
          onClick={onStartOver}
          className="pulse-start-over"
        >
          <Typography variant="Body50.Medium">
            Start over
          </Typography>
        </button>
      )}
    </div>
  )
}
