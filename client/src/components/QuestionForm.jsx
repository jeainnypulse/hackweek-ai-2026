export default function QuestionForm({
  question,
  onQuestionChange,
  onSubmit,
  loading,
  inConversation,
}) {
  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      onSubmit()
    }
  }

  return (
    <div className="pulse-composer">
      <textarea
        rows={4}
        placeholder="Ask a payer question..."
        className="pulse-composer-input"
        value={question}
        onChange={event => onQuestionChange(event.target.value)}
        onKeyDown={handleKeyDown}
        disabled={inConversation || loading}
      />
    </div>
  )
}
