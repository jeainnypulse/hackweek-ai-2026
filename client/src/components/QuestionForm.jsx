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
    <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3">
      <textarea
        rows={3}
        placeholder="Ask a payer question — e.g. What is Calquence's QoA for 1L CLL under commercial plans?"
        className="w-full text-sm text-gray-800 placeholder-gray-400 resize-none outline-none disabled:opacity-50"
        value={question}
        onChange={event => onQuestionChange(event.target.value)}
        onKeyDown={handleKeyDown}
        disabled={inConversation || loading}
      />
      <div className="flex items-center justify-end mt-3 pt-3 border-t border-gray-100">
        <button
          onClick={onSubmit}
          disabled={loading || !question.trim() || inConversation}
          className="text-sm bg-violet-600 hover:bg-violet-700 disabled:opacity-40 disabled:cursor-not-allowed text-white px-4 py-1.5 rounded-lg transition-colors"
        >
          {loading && !inConversation ? 'Thinking...' : 'Ask'}
        </button>
      </div>
    </div>
  )
}
