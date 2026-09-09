import { Colors, Typography } from '@pulse-analytics/pulse-design-system'

import AnswerView from './AnswerView'
import ClarificationForm from './ClarificationForm'
import PulseAiIcon from './PulseAiIcon'

export default function ResultCard({
  result,
  loading,
  question,
  slotTurns,
  slotInput,
  onSlotInputChange,
  onSubmitSlot,
  onFeedback,
  onStartOver,
}) {
  if (!result && !loading) return null

  const isComplete = result?.status === 'complete'

  return (
    <div className="pulse-conversation">
      <div
        className="pulse-question-bubble"
        style={{ backgroundColor: Colors.Primary }}
      >
        <Typography variant="Body50.Regular">{question}</Typography>
      </div>

      {loading && !result ? (
        <div
          className="pulse-response-row"
          role="status"
          aria-live="polite"
          style={{ color: Colors.Black }}
        >
          <PulseAiIcon className="pulse-response-icon" />
          <Typography variant="Body50.Regular">
            Searching Quality of Access records...
          </Typography>
          <span className="pulse-loading-spinner" aria-hidden="true" />
        </div>
      ) : isComplete ? (
        <>
          <ClarificationForm
            result={result}
            turns={slotTurns}
            slotInput={slotInput}
            loading={loading}
            onSlotInputChange={onSlotInputChange}
            onSubmit={onSubmitSlot}
            onStartOver={onStartOver}
          />
          <div
            className="pulse-response-row pulse-response-row-answer"
            style={{ color: Colors.Black }}
          >
            <PulseAiIcon className="pulse-response-icon" />
            <AnswerView result={result} onFeedback={onFeedback} />
          </div>
        </>
      ) : (
        <ClarificationForm
          result={result}
          turns={slotTurns}
          slotInput={slotInput}
          loading={loading}
          onSlotInputChange={onSlotInputChange}
          onSubmit={onSubmitSlot}
          onStartOver={onStartOver}
        />
      )}
    </div>
  )
}
