export function formatApiError(message) {
  if (/token has expired|sso|credentials/i.test(message)) {
    return "AWS SSO session expired. In your terminal run: aws sso login — then restart the server with python main.py.";
  }
  return message;
}

function parseErrorDetail(data, fallback) {
  if (!data?.detail) return fallback;
  if (typeof data.detail === "string") return formatApiError(data.detail);
  if (Array.isArray(data.detail)) {
    return formatApiError(
      data.detail.map((item) => item.msg || item).join(", "),
    );
  }
  return formatApiError(String(data.detail));
}

async function postJson(path, body, fallbackError) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(parseErrorDetail(data, fallbackError));
  }
  return data;
}

export function askQuestion(question, guidedIntent = null) {
  return postJson(
    "/api/ask",
    {
      question,
      guided: Boolean(guidedIntent),
      guided_intent: guidedIntent,
    },
    "Something went wrong",
  );
}

export function continueConversation({ question, sessionId, value }) {
  return postJson(
    "/api/ask",
    {
      question,
      session_id: sessionId,
      action: "provide",
      value,
    },
    "Something went wrong",
  );
}

export function sendFeedback(id, rating) {
  return postJson("/api/feedback", { id, rating }, "Could not save feedback");
}
