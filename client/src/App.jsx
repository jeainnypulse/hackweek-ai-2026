import { useState } from "react";
import { Colors, Typography, Icon } from "@pulse-analytics/pulse-design-system";

import AppHeader from "./components/AppHeader";
import ChatHistory from "./components/ChatHistory";
import PulseAiIcon from "./components/PulseAiIcon";
import QuestionForm from "./components/QuestionForm";
import ResultCard from "./components/ResultCard";
import useChat from "./hooks/useChat";

const SUGGESTED_QUESTIONS = [
  "What is a payer's commercial coverage for my regimen?",
  "QoA for a specific regimen, line and indication",
  "Which payers restrict a regimen more than the FDA label?",
];

export default function App() {
  const chat = useChat();
  const [panelOpen, setPanelOpen] = useState(false);

  return (
    <main className="pulse-workspace">
      <div
        className="pulse-dashboard"
        role="img"
        aria-label="Pulse Quality of Access dashboard"
      />

      {!panelOpen && (
        <button
          type="button"
          className="pulse-launcher"
          onClick={() => setPanelOpen(true)}
          aria-label="Open Pulse AI"
        >
          <PulseAiIcon className="pulse-mark" />
          <Typography variant="Body50.Semibold">Ask Pulse AI</Typography>
        </button>
      )}

      {panelOpen && (
        <>
          <button
            type="button"
            className="pulse-drawer-scrim"
            onClick={() => setPanelOpen(false)}
            aria-label="Close Pulse AI"
          />
          <aside className="pulse-drawer" aria-label="Pulse AI chatbot">
            <header className="pulse-drawer-header">
              <Typography variant="Header200.Bold">Pulse AI</Typography>
              <button
                type="button"
                className="pulse-close"
                style={{ backgroundColor: Colors.GrayCool50 }}
                onClick={() => setPanelOpen(false)}
                aria-label="Close Pulse AI"
              >
                <Icon
                  id="close"
                  width={12}
                  height={12}
                  iconColors={{ color1: Colors.Gray500 }}
                />
              </button>
            </header>

            <div className="pulse-drawer-content">
              {!chat.result && !chat.loading && !chat.error && (
                <section className="pulse-welcome">
                  <PulseAiIcon className="pulse-welcome-mark" />
                  <AppHeader />
                  <div className="pulse-suggestions">
                    {SUGGESTED_QUESTIONS.map((question) => (
                      <button
                        type="button"
                        key={question}
                        onClick={() => chat.ask(question)}
                        style={{
                          backgroundColor: `${Colors.Primary}1A`,
                          color: Colors.Primary,
                        }}
                      >
                        <Typography variant="Body50.Semibold">
                          {question}
                        </Typography>
                        <Typography
                          variant="Header300.Medium"
                          aria-hidden="true"
                        >
                          ›
                        </Typography>
                      </button>
                    ))}
                  </div>
                </section>
              )}

              {chat.error && (
                <div className="pulse-error">
                  <Typography variant="Body50.Regular">{chat.error}</Typography>
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

            <footer className="pulse-drawer-footer">
              <Typography
                variant="Body30.Regular"
                style={{ color: Colors.Gray500 }}
              >
                Or input your own:
              </Typography>
              <QuestionForm
                question={chat.question}
                onQuestionChange={chat.setQuestion}
                onSubmit={chat.ask}
                loading={chat.loading}
                inConversation={chat.inConversation}
              />
              <Typography
                variant="Body25.Regular"
                className="pulse-support-copy"
              >
                If you need assistance, please contact{" "}
                <Typography
                  variant="Body25.Regular"
                  href="mailto:support@pulse.norstella.com"
                >
                  support@pulse.norstella.com
                </Typography>
              </Typography>
            </footer>
          </aside>
        </>
      )}
    </main>
  );
}
