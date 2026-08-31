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
        <span className="text-xs font-medium text-violet-600 uppercase tracking-wide">
          {isComplete
            ? 'Answer'
            : 'Clarify slots'}
        </span>
        {isComplete && (
          <span className="text-xs text-gray-400">
            {result.records_retrieved} records
          </span>
        )}
      </div>

      {result.question && (
        <p className="text-sm text-gray-500 mb-3 pb-3 border-b border-gray-100">
          <span className="text-xs font-medium text-gray-400 uppercase tracking-wide mr-2">
            Question
          </span>
          {result.question}
        </p>
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
