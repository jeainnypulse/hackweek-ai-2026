import { Colors } from '@pulse-analytics/pulse-design-system'

export default function QuestionForm({
  question,
  onQuestionChange,
  onSubmit,
  loading,
  inConversation,
}) {
  function handleKeyDown(event) {
    if (
      event.key === 'Enter' &&
      !event.shiftKey &&
      question.trim() &&
      !inConversation &&
      !loading
    ) {
      event.preventDefault()
      onSubmit()
    }
  }

  const submitDisabled = loading || inConversation || !question.trim()

  return (
    <div className="pulse-composer">
      <textarea
        rows={4}
        placeholder="Ask a payer question..."
        className="pulse-composer-input"
        style={{ color: question ? Colors.Black : undefined }}
        value={question}
        onChange={event => onQuestionChange(event.target.value)}
        onKeyDown={handleKeyDown}
        disabled={inConversation || loading}
      />
      <button
        type="button"
        className="pulse-composer-send"
        onClick={() => onSubmit()}
        disabled={submitDisabled}
        style={{
          backgroundColor: submitDisabled ? undefined : Colors.Primary,
        }}
        aria-label="Send question"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="m4 4 17 8-17 8 3-7 8-1-8-1-3-7Z" />
        </svg>
      </button>
    </div>
  )
}
