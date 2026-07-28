"""
Exact match retrieval using a fine-tuned T5 slot extractor.
Standalone — does not share code with exact_match.py.

Called when retrieval_method = "exact_match_finetuned"
"""

import os
import json
from db import get_db
from entity_linking import link_slots

MODEL_PATH = os.getenv("FINETUNED_MODEL_PATH", "models/slot_extractor")
# cascade = synonym -> catalog -> LLM hypothesize+Mongo verify (production)
ENTITY_LINK_METHOD = os.getenv("ENTITY_LINK_METHOD", "cascade")

_model = None
_tokenizer = None


def _load_model():
    global _model, _tokenizer
    if _model is None:
        from transformers import T5ForConditionalGeneration, T5Tokenizer

        print(f"[exact_match_finetuned] loading model from {MODEL_PATH}...")
        _tokenizer = T5Tokenizer.from_pretrained(MODEL_PATH)
        _model = T5ForConditionalGeneration.from_pretrained(MODEL_PATH)
        _model = _model.to("cpu")
        _model.eval()
        print("[exact_match_finetuned] model ready.")
    return _model, _tokenizer


def extract_slots_finetuned(question: str) -> dict:
    import torch

    model, tokenizer = _load_model()
    input_text = f"extract slots: {question}"
    inputs = tokenizer(input_text, return_tensors="pt", max_length=128, truncation=True)
    inputs = {k: v.to("cpu") for k, v in inputs.items()}
    with torch.no_grad():
        outputs = model.generate(**inputs, max_length=256)
    predicted = tokenizer.decode(outputs[0], skip_special_tokens=True)
    clean = predicted.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(clean)


def exact_match_finetuned_retrieval(question: str) -> list[dict]:
    slots = extract_slots_finetuned(question)
    print(f"[exact_match_finetuned] slots: {slots}")
    linked, link_info = link_slots(slots, method=ENTITY_LINK_METHOD)
    if link_info:
        print(f"[exact_match_finetuned] linked ({ENTITY_LINK_METHOD}): {link_info}")
    query = {k: v for k, v in linked.items() if v is not None}
    if not query:
        return []
    db = get_db()
    records = list(db.payerLatestAccess.find(query, {"_id": 0}).limit(50))
    print(f"[exact_match_finetuned] {len(records)} records found")
    return records
