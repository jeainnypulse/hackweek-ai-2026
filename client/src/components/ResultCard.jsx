import { Typography } from '@pulse-analytics/pulse-design-system'

import AnswerView from './AnswerView'
import ClarificationForm from './ClarificationForm'

export default function ResultCard({
  result,
  loading,
  slotInput,
  onSlotInputChange,
  onSubmitSlot,
  onFeedback,
  onStartOver,
}) {
  if (!result) return null

  const isComplete = result.status === 'complete'

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 mb-6">
      <div className="flex items-center justify-between mb-3">
        <Typography
          variant="Overline50.Medium"
          className="text-violet-600"
        >
          {isComplete
            ? 'Answer'
            : 'Clarify slots'}
        </Typography>
        {isComplete && (
          <Typography
            variant="Body50.Regular"
            className="text-gray-400"
          >
            {result.records_retrieved} records
          </Typography>
        )}
      </div>

      {result.question && (
        <Typography
          variant="Body100.Regular"
          className="text-gray-500 mb-3 pb-3 border-b border-gray-100"
        >
          <Typography
            variant="Overline50.Medium"
            className="text-gray-400 mr-2"
          >
            Question
          </Typography>
          {result.question}
        </Typography>
      )}

      {isComplete ? (
        <AnswerView result={result} onFeedback={onFeedback} />
      ) : (
        <ClarificationForm
          result={result}
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
