#!/bin/bash
set -e

echo "Setting up hackweek-ai-chatbot..."

# ── Directory structure ──────────────────────────────────────────
mkdir -p client/src
mkdir -p server/retrieval
mkdir -p server/scripts
touch server/retrieval/__init__.py

# ── SERVER FILES ─────────────────────────────────────────────────

cat > server/.env << 'EOF'
MONGO_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net
MONGO_DB=pulse
AWS_REGION=us-east-1
BEDROCK_CLAUDE_MODEL=anthropic.claude-sonnet-4-6
BEDROCK_EMBEDDING_MODEL=amazon.titan-embed-text-v2:0
EOF

cat > server/requirements.txt << 'EOF'
fastapi==0.104.1
uvicorn==0.24.0
pymongo==4.6.0
boto3==1.34.0
python-dotenv==1.0.0
pydantic==2.5.0
python-pptx==0.6.21
python-docx==1.1.0
EOF

cat > server/db.py << 'EOF'
from pymongo import MongoClient
from functools import lru_cache
import os

@lru_cache()
def get_client():
    uri = os.getenv("MONGO_URI")
    if not uri:
        raise RuntimeError("MONGO_URI is not set in .env")
    return MongoClient(uri)

def get_db():
    return get_client()[os.getenv("MONGO_DB", "pulse")]
EOF

cat > server/bedrock.py << 'EOF'
import boto3
import json
import os
from functools import lru_cache

@lru_cache()
def get_bedrock_client():
    return boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))

def invoke_claude(prompt: str, system: str = "") -> str:
    client = get_bedrock_client()
    model_id = os.getenv("BEDROCK_CLAUDE_MODEL", "anthropic.claude-sonnet-4-6")
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    response = client.invoke_model(modelId=model_id, body=json.dumps(body))
    result = json.loads(response["body"].read())
    return result["content"][0]["text"]

def get_embedding(text: str) -> list[float]:
    client = get_bedrock_client()
    model_id = os.getenv("BEDROCK_EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0")
    response = client.invoke_model(modelId=model_id, body=json.dumps({"inputText": text}))
    result = json.loads(response["body"].read())
    return result["embedding"]
EOF

cat > server/feedback.py << 'EOF'
from db import get_db
from datetime import datetime

def save_feedback(result_id: str, rating: str):
    db = get_db()
    db.logs.update_one(
        {"_id": result_id},
        {"$set": {"feedback": rating, "feedback_at": datetime.utcnow()}}
    )
EOF

cat > server/synthesis.py << 'EOF'
from bedrock import invoke_claude
import json

SYSTEM_PROMPT = """You are a payer data analyst assistant for a pharmaceutical market access consulting firm.

Answer questions about payer Quality of Access (QoA) data using ONLY the records provided. Never make up data.

Rules:
- Only use information from the provided records.
- Cite every claim with its source (payer, drug, indication, report period).
- If records are empty or insufficient, say so clearly. Do not guess.
- If data is partial, say so explicitly.
- Be concise. Lead with the answer, then support with data.
- Format lives percentages clearly (e.g. 14% of commercial lives)."""

def synthesize_answer(question: str, records: list[dict]) -> tuple[str, list[str]]:
    if not records:
        return (
            "No matching records were found for this question. "
            "Please check the drug name, indication, and book of business and try again.",
            []
        )

    payer_records = [r for r in records if "chunk_type" not in r]
    doc_chunks = [r for r in records if r.get("chunk_type") == "document"]

    context_parts = []
    if payer_records:
        context_parts.append("PAYER DATA RECORDS:")
        for r in payer_records:
            context_parts.append(json.dumps(r, default=str))
    if doc_chunks:
        context_parts.append("\nDOCUMENT EXCERPTS (supporting context):")
        for chunk in doc_chunks:
            label = f"[{chunk.get('source_file', 'unknown')} — slide/page {chunk.get('slide_or_page', '?')}]"
            context_parts.append(f"{label}\n{chunk.get('text', '')}")

    context = "\n".join(context_parts)
    prompt = f"""Answer this question using only the records below. Cite your sources.

Question: {question}

Records:
{context}"""

    answer = invoke_claude(prompt, system=SYSTEM_PROMPT)

    citations = []
    for r in payer_records:
        parts = [p for p in [
            r.get("payer"), r.get("drug"), r.get("indication"),
            r.get("book_of_business"), r.get("report_period"),
        ] if p]
        if parts:
            citations.append(" · ".join(str(p) for p in parts))

    return answer, list(dict.fromkeys(citations))
EOF

cat > server/retrieval/exact_match.py << 'EOF'
from bedrock import invoke_claude
from db import get_db
import json

SLOT_EXTRACTION_PROMPT = """Extract structured slots from this payer question. Return ONLY valid JSON with no explanation or markdown.

Slots to extract:
- drug: brand name (Calquence, Brukinsa, Imbruvica, Imfinzi). Return null if not specified.
- indication: line of therapy + disease (e.g. 1L CLL, 2L CLL, Stage III NSCLC). Return null if not specified.
- book_of_business: "commercial" or "medicare". Default to "commercial" if not mentioned.
- payer: specific payer name if mentioned, otherwise null.

Drug mappings:
- cal, calquence, acalabrutinib -> Calquence
- brukinsa, zanubrutinib -> Brukinsa
- imbruvica, ibrutinib -> Imbruvica
- imfinzi, durvalumab -> Imfinzi

Indication mappings:
- first line CLL, 1L CLL -> 1L CLL
- second line CLL, 2L CLL -> 2L CLL
- stage 3 NSCLC, stage III NSCLC -> Stage III NSCLC

Return format:
{"drug": "Calquence", "indication": "1L CLL", "book_of_business": "commercial", "payer": null}

Question: """

def extract_slots(question: str) -> dict:
    response = invoke_claude(SLOT_EXTRACTION_PROMPT + question)
    clean = response.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(clean)

def exact_match_retrieval(question: str) -> list[dict]:
    slots = extract_slots(question)
    print(f"[exact_match] slots: {slots}")
    query = {k: v for k, v in slots.items() if v is not None}
    if not query:
        return []
    db = get_db()
    records = list(db.payerLatestAccess.find(query, {"_id": 0}).limit(50))
    print(f"[exact_match] {len(records)} records found")
    return records
EOF

cat > server/retrieval/vector_search.py << 'EOF'
from bedrock import get_embedding
from db import get_db

def vector_search_retrieval(question: str, top_k: int = 10) -> list[dict]:
    embedding = get_embedding(question)
    db = get_db()
    pipeline = [
        {
            "$vectorSearch": {
                "index": "qoa_vector_index",
                "path": "embedding",
                "queryVector": embedding,
                "numCandidates": top_k * 10,
                "limit": top_k,
            }
        },
        {
            "$project": {
                "_id": 0,
                "score": {"$meta": "vectorSearchScore"},
                "drug": 1,
                "indication": 1,
                "book_of_business": 1,
                "payer": 1,
                "qoa_segment": 1,
                "lives_pct": 1,
                "report_period": 1,
            }
        }
    ]
    records = list(db.payerLatestAccess.aggregate(pipeline))
    print(f"[vector_search] {len(records)} records found")
    return records
EOF

cat > server/retrieval/combined.py << 'EOF'
from retrieval.exact_match import exact_match_retrieval
from bedrock import get_embedding
from db import get_db

def combined_retrieval(question: str) -> list[dict]:
    payer_records = exact_match_retrieval(question)
    doc_chunks = retrieve_doc_chunks(question)
    print(f"[combined] {len(payer_records)} payer records + {len(doc_chunks)} doc chunks")
    return payer_records + doc_chunks

def retrieve_doc_chunks(question: str, top_k: int = 5) -> list[dict]:
    embedding = get_embedding(question)
    db = get_db()
    pipeline = [
        {
            "$vectorSearch": {
                "index": "doc_chunks_vector_index",
                "path": "embedding",
                "queryVector": embedding,
                "numCandidates": top_k * 10,
                "limit": top_k,
            }
        },
        {
            "$project": {
                "_id": 0,
                "score": {"$meta": "vectorSearchScore"},
                "text": 1,
                "source_file": 1,
                "slide_or_page": 1,
                "chunk_type": {"$literal": "document"},
            }
        }
    ]
    return list(db.doc_chunks.aggregate(pipeline))
EOF

cat > server/scripts/embed_records.py << 'EOF'
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from db import get_db
from bedrock import get_embedding
import time

def flatten_record(record: dict) -> str:
    parts = []
    if record.get("payer"):            parts.append(record["payer"])
    if record.get("drug"):             parts.append(record["drug"])
    if record.get("indication"):       parts.append(f"indication {record['indication']}")
    if record.get("book_of_business"): parts.append(record["book_of_business"])
    if record.get("qoa_segment"):      parts.append(f"QoA {record['qoa_segment']}")
    if record.get("lives_pct"):        parts.append(f"{record['lives_pct']}% of lives")
    if record.get("report_period"):    parts.append(f"report period {record['report_period']}")
    return " ".join(parts)

def main():
    db = get_db()
    collection = db.payerLatestAccess
    total = collection.count_documents({"embedding": {"$exists": False}})
    print(f"Records to embed: {total}")
    if total == 0:
        print("All records already have embeddings.")
        return
    for i, record in enumerate(collection.find({"embedding": {"$exists": False}})):
        text = flatten_record(record)
        embedding = get_embedding(text)
        collection.update_one({"_id": record["_id"]}, {"$set": {"embedding": embedding, "embedding_text": text}})
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{total}")
            time.sleep(0.3)
    print(f"Done. {total} records embedded.")
    print("\nNow create a vector search index in MongoDB Atlas:")
    print("  Collection: payerLatestAccess | Index: qoa_vector_index | Field: embedding | Dimensions: 1024 | Similarity: cosine")

if __name__ == "__main__":
    main()
EOF

cat > server/scripts/embed_docs.py << 'EOF'
import sys, os, argparse
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from db import get_db
from bedrock import get_embedding
import time

def extract_pptx_chunks(filepath):
    from pptx import Presentation
    prs = Presentation(filepath)
    chunks = []
    for i, slide in enumerate(prs.slides):
        text = "\n".join(shape.text.strip() for shape in slide.shapes if hasattr(shape, "text") and shape.text.strip())
        if text.strip():
            chunks.append({"text": text, "source_file": os.path.basename(filepath), "slide_or_page": i + 1, "file_type": "pptx"})
    return chunks

def extract_docx_chunks(filepath, chunk_size=5):
    from docx import Document
    doc = Document(filepath)
    chunks, buffer = [], []
    for para in doc.paragraphs:
        if para.text.strip():
            buffer.append(para.text.strip())
            if len(buffer) >= chunk_size:
                chunks.append({"text": "\n".join(buffer), "source_file": os.path.basename(filepath), "slide_or_page": len(chunks) + 1, "file_type": "docx"})
                buffer = []
    if buffer:
        chunks.append({"text": "\n".join(buffer), "source_file": os.path.basename(filepath), "slide_or_page": len(chunks) + 1, "file_type": "docx"})
    return chunks

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs_dir", required=True)
    args = parser.parse_args()
    db = get_db()
    all_chunks = []
    for filename in os.listdir(args.docs_dir):
        filepath = os.path.join(args.docs_dir, filename)
        if filename.endswith(".pptx"):
            print(f"Parsing {filename}...")
            all_chunks.extend(extract_pptx_chunks(filepath))
        elif filename.endswith(".docx"):
            print(f"Parsing {filename}...")
            all_chunks.extend(extract_docx_chunks(filepath))
    print(f"\nEmbedding {len(all_chunks)} chunks...")
    for i, chunk in enumerate(all_chunks):
        chunk["embedding"] = get_embedding(chunk["text"])
        db.doc_chunks.insert_one(chunk)
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(all_chunks)}")
        time.sleep(0.3)
    print("Done.")
    print("\nNow create a vector search index in MongoDB Atlas:")
    print("  Collection: doc_chunks | Index: doc_chunks_vector_index | Field: embedding | Dimensions: 1024 | Similarity: cosine")

if __name__ == "__main__":
    main()
EOF

cat > server/main.py << 'EOF'
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import uuid

load_dotenv()

from retrieval.exact_match import exact_match_retrieval
from retrieval.vector_search import vector_search_retrieval
from retrieval.combined import combined_retrieval
from synthesis import synthesize_answer
from db import get_db
from feedback import save_feedback

app = FastAPI(title="Pulse AI Chatbot", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class AskRequest(BaseModel):
    question: str
    retrieval_method: str = "exact_match"

class FeedbackRequest(BaseModel):
    id: str
    rating: str

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.post("/api/ask")
def ask(req: AskRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    if req.retrieval_method == "exact_match":
        records = exact_match_retrieval(req.question)
    elif req.retrieval_method == "vector_search":
        records = vector_search_retrieval(req.question)
    elif req.retrieval_method == "combined":
        records = combined_retrieval(req.question)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown retrieval method: {req.retrieval_method}")
    answer, citations = synthesize_answer(req.question, records)
    result_id = str(uuid.uuid4())
    db = get_db()
    db.logs.insert_one({
        "_id": result_id,
        "question": req.question,
        "retrieval_method": req.retrieval_method,
        "records_retrieved": len(records),
        "answer": answer,
        "citations": citations,
        "feedback": None,
    })
    return {
        "id": result_id,
        "answer": answer,
        "citations": citations,
        "retrieval_method": req.retrieval_method,
        "records_retrieved": len(records),
    }

@app.post("/api/feedback")
def feedback(req: FeedbackRequest):
    save_feedback(req.id, req.rating)
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
EOF

# ── CLIENT FILES ─────────────────────────────────────────────────

cat > client/package.json << 'EOF'
{
  "name": "hackweek-ai-chatbot-client",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.0.0",
    "vite": "^4.4.0",
    "tailwindcss": "^3.3.0",
    "autoprefixer": "^10.4.14",
    "postcss": "^8.4.27"
  }
}
EOF

cat > client/vite.config.js << 'EOF'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
EOF

cat > client/tailwind.config.js << 'EOF'
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: { extend: {} },
  plugins: [],
}
EOF

cat > client/postcss.config.js << 'EOF'
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
EOF

cat > client/index.html << 'EOF'
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Pulse AI Chatbot</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
EOF

cat > client/src/main.jsx << 'EOF'
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
EOF

cat > client/src/index.css << 'EOF'
@tailwind base;
@tailwind components;
@tailwind utilities;
EOF

cat > client/src/App.jsx << 'EOF'
import { useState } from 'react'

const RETRIEVAL_METHODS = [
  { value: 'exact_match', label: 'Exact Match' },
  { value: 'vector_search', label: 'Vector Search' },
  { value: 'combined', label: 'Combined (Mongo + Docs)' },
]

export default function App() {
  const [question, setQuestion] = useState('')
  const [method, setMethod] = useState('exact_match')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [history, setHistory] = useState([])

  async function ask() {
    if (!question.trim()) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, retrieval_method: method }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Something went wrong')
      setResult(data)
      setHistory(prev => [{ question, method, result: data }, ...prev])
      setQuestion('')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function submitFeedback(id, rating) {
    await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, rating }),
    })
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-2xl mx-auto px-4 py-12">
        <div className="mb-10">
          <h1 className="text-xl font-semibold text-gray-900">Pulse AI Chatbot</h1>
          <p className="text-sm text-gray-400 mt-0.5">Phase 1 POC — Internal only</p>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3">
          <textarea
            rows={3}
            placeholder="Ask a payer question — e.g. What is Calquence's QoA for 1L CLL under commercial plans?"
            className="w-full text-sm text-gray-800 placeholder-gray-400 resize-none outline-none"
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask() } }}
          />
          <div className="flex items-center justify-between mt-3 pt-3 border-t border-gray-100">
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-400">Retrieval:</span>
              <select
                value={method}
                onChange={e => setMethod(e.target.value)}
                className="text-xs border border-gray-200 rounded-md px-2 py-1 text-gray-600 outline-none bg-white"
              >
                {RETRIEVAL_METHODS.map(m => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </select>
            </div>
            <button
              onClick={ask}
              disabled={loading || !question.trim()}
              className="text-sm bg-violet-600 hover:bg-violet-700 disabled:opacity-40 disabled:cursor-not-allowed text-white px-4 py-1.5 rounded-lg transition-colors"
            >
              {loading ? 'Thinking...' : 'Ask'}
            </button>
          </div>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl p-4 mb-3">
            {error}
          </div>
        )}

        {result && (
          <div className="bg-white border border-gray-200 rounded-xl p-4 mb-6">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-medium text-violet-600 uppercase tracking-wide">Answer</span>
              <span className="text-xs text-gray-400">{result.retrieval_method} · {result.records_retrieved} records</span>
            </div>
            <p className="text-sm text-gray-800 leading-relaxed whitespace-pre-wrap">{result.answer}</p>
            {result.citations?.length > 0 && (
              <div className="mt-4 pt-3 border-t border-gray-100">
                <p className="text-xs text-gray-400 mb-2">Sources</p>
                <div className="flex flex-col gap-1">
                  {result.citations.map((c, i) => (
                    <div key={i} className="text-xs text-gray-500 bg-gray-50 rounded px-2 py-1 font-mono">{c}</div>
                  ))}
                </div>
              </div>
            )}
            <div className="mt-4 pt-3 border-t border-gray-100 flex items-center gap-3">
              <span className="text-xs text-gray-400">Would you send this to a client?</span>
              <button onClick={() => submitFeedback(result.id, 'yes')} className="text-xs px-3 py-1 rounded-md border border-gray-200 hover:bg-green-50 hover:border-green-300 hover:text-green-700 transition-colors">👍 Yes</button>
              <button onClick={() => submitFeedback(result.id, 'no')} className="text-xs px-3 py-1 rounded-md border border-gray-200 hover:bg-red-50 hover:border-red-300 hover:text-red-700 transition-colors">👎 No</button>
            </div>
          </div>
        )}

        {history.length > 0 && (
          <div>
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-3">History</p>
            <div className="flex flex-col gap-2">
              {history.map((h, i) => (
                <button key={i} onClick={() => setResult(h.result)} className="text-left bg-white border border-gray-200 rounded-lg px-3 py-2.5 hover:border-violet-200 transition-colors">
                  <p className="text-sm text-gray-700 truncate">{h.question}</p>
                  <p className="text-xs text-gray-400 mt-0.5">{h.method}</p>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
EOF

echo ""
echo "✅ All files created successfully!"
echo ""
echo "Next steps:"
echo "  1. Fill in your MONGO_URI in server/.env"
echo "  2. cd server && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt && python main.py"
echo "  3. cd client && npm install && npm run dev"