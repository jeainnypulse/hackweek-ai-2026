import AppHeader from './components/AppHeader'
import ChatHistory from './components/ChatHistory'
import QuestionForm from './components/QuestionForm'
import ResultCard from './components/ResultCard'
import useChat from './hooks/useChat'

export default function App() {
  const chat = useChat()

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-4xl mx-auto px-4 py-12">
        <AppHeader />

        <QuestionForm
          question={chat.question}
          onQuestionChange={chat.setQuestion}
          onSubmit={chat.ask}
          loading={chat.loading}
          inConversation={chat.inConversation}
        />

        {chat.error && (
          <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl p-4 mb-3">
            {chat.error}
          </div>
        )}

        <ResultCard
          result={chat.result}
          loading={chat.loading}
          slotInput={chat.slotInput}
          onSlotInputChange={chat.setSlotInput}
          onSubmitSlot={chat.submitSlotValue}
          onFeedback={chat.submitFeedback}
          onStartOver={chat.startOver}
        />

        <ChatHistory
          history={chat.history}
          onSelect={chat.showHistoryItem}
        />
      </div>
    </div>
  )
}
