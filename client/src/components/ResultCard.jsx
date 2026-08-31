import AnswerView from './AnswerView'
import CascadeForm from './CascadeForm'
import ClarificationForm from './ClarificationForm'

export default function ResultCard({
  result,
  loading,
  slotInput,
  cascadeSelections,
  cascadeReady,
  onSlotInputChange,
  onSubmitSlot,
  onSessionAction,
  onCascadeChange,
  onSubmitCascade,
  onFeedback,
  onStartOver,
}) {
  if (!result) return null

  const isComplete = result.status === 'complete'
  const isCascade = result.status === 'needs_cascade_input'

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 mb-6">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-medium text-violet-600 uppercase tracking-wide">
          {isComplete
            ? 'Answer'
            : isCascade
              ? 'Additional details'
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
      ) : isCascade ? (
        <CascadeForm
          result={result}
          selections={cascadeSelections}
          loading={loading}
          ready={cascadeReady}
          onChange={onCascadeChange}
          onSubmit={onSubmitCascade}
        />
      ) : (
        <ClarificationForm
          result={result}
          slotInput={slotInput}
          loading={loading}
          onSlotInputChange={onSlotInputChange}
          onSubmit={onSubmitSlot}
          onSkip={() => onSessionAction('skip', null)}
          onStartOver={onStartOver}
        />
      )}
    </div>
  )
}
