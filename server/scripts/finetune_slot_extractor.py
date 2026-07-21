"""
Fine-tune T5-small to extract structured slots from payer questions.

This replaces the Claude-based slot extraction in exact_match.py with a
small model that has learned your specific payer vocabulary and aliases.

Usage:
  cd server
  source venv/bin/activate
  pip install transformers datasets torch accelerate scikit-learn
  python scripts/finetune_slot_extractor.py --data scripts/training_data.json

After training, the model is saved to server/models/slot_extractor/
"""

import argparse
import json
import os
import sys

# ── Args ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--data", required=True, help="Path to training_data.json")
parser.add_argument(
    "--output_dir", default="models/slot_extractor", help="Where to save the model"
)
parser.add_argument("--epochs", type=int, default=10, help="Number of training epochs")
parser.add_argument("--batch_size", type=int, default=8, help="Training batch size")
parser.add_argument("--lr", type=float, default=3e-4, help="Learning rate")
args = parser.parse_args()

# ── Imports ───────────────────────────────────────────────────────────────────
print("Loading libraries...")
import torch
from transformers import (
    T5ForConditionalGeneration,
    T5Tokenizer,
    Seq2SeqTrainer,
    Seq2SeqTrainingArguments,
    DataCollatorForSeq2Seq,
)
from datasets import Dataset
from sklearn.model_selection import train_test_split

# ── Load data ─────────────────────────────────────────────────────────────────
print(f"Loading data from {args.data}...")
with open(args.data) as f:
    raw = json.load(f)

# Format: input = "extract slots: <question>", output = "<slots json>"
examples = [
    {
        "input": f"extract slots: {p['question']}",
        "output": (
            p["expected_slots"]
            if isinstance(p["expected_slots"], str)
            else json.dumps(p["expected_slots"])
        ),
    }
    for p in raw
]

print(f"Total examples: {len(examples)}")

# Train/val split — 85/15
train_data, val_data = train_test_split(examples, test_size=0.15, random_state=42)
print(f"Train: {len(train_data)} | Val: {len(val_data)}")

# ── Load model + tokenizer ────────────────────────────────────────────────────
MODEL_NAME = "t5-small"
print(f"Loading {MODEL_NAME}...")
tokenizer = T5Tokenizer.from_pretrained(MODEL_NAME)
model = T5ForConditionalGeneration.from_pretrained(MODEL_NAME)

# ── Tokenize ──────────────────────────────────────────────────────────────────
MAX_INPUT_LEN = 128
MAX_OUTPUT_LEN = 128


def tokenize(batch):
    model_inputs = tokenizer(
        batch["input"],
        max_length=MAX_INPUT_LEN,
        padding="max_length",
        truncation=True,
    )
    labels = tokenizer(
        batch["output"],
        max_length=MAX_OUTPUT_LEN,
        padding="max_length",
        truncation=True,
    )
    # Replace padding token id in labels with -100 so loss ignores them
    label_ids = [
        [(l if l != tokenizer.pad_token_id else -100) for l in label]
        for label in labels["input_ids"]
    ]
    model_inputs["labels"] = label_ids
    return model_inputs


print("Tokenizing...")
train_dataset = Dataset.from_list(train_data).map(
    tokenize, batched=True, remove_columns=["input", "output"]
)
val_dataset = Dataset.from_list(val_data).map(
    tokenize, batched=True, remove_columns=["input", "output"]
)

# ── Training args ─────────────────────────────────────────────────────────────
os.makedirs(args.output_dir, exist_ok=True)

training_args = Seq2SeqTrainingArguments(
    output_dir=args.output_dir,
    num_train_epochs=args.epochs,
    per_device_train_batch_size=args.batch_size,
    per_device_eval_batch_size=args.batch_size,
    learning_rate=args.lr,
    warmup_steps=10,
    weight_decay=0.01,
    logging_dir=os.path.join(args.output_dir, "logs"),
    logging_steps=10,
    eval_strategy="epoch",
    save_strategy="epoch",
    load_best_model_at_end=True,
    predict_with_generate=True,
    fp16=False,  # CPU training — no fp16
    report_to="none",
)

data_collator = DataCollatorForSeq2Seq(tokenizer, model=model, padding=True)

# ── Train ─────────────────────────────────────────────────────────────────────
print(f"\nStarting training — {args.epochs} epochs on {len(train_data)} examples...")
print("This will take a few minutes on CPU.\n")

trainer = Seq2SeqTrainer(
    model=model,
    args=training_args,
    train_dataset=train_dataset,
    eval_dataset=val_dataset,
    tokenizer=tokenizer,
    data_collator=data_collator,
)

trainer.train()

# ── Save best model ───────────────────────────────────────────────────────────
print(f"\nSaving model to {args.output_dir}...")
model.save_pretrained(args.output_dir)
tokenizer.save_pretrained(args.output_dir)
print("Model saved.")

# ── Quick eval on val set ─────────────────────────────────────────────────────
print("\nRunning quick eval on validation set...")
model.eval()
correct = 0
for ex in val_data:
    inputs = tokenizer(
        ex["input"], return_tensors="pt", max_length=MAX_INPUT_LEN, truncation=True
    )
    with torch.no_grad():
        outputs = model.generate(**inputs, max_length=MAX_OUTPUT_LEN)
    predicted = tokenizer.decode(outputs[0], skip_special_tokens=True)
    expected = ex["output"]

    try:
        pred_json = json.loads(predicted)
        exp_json = json.loads(expected)
        if pred_json == exp_json:
            correct += 1
        else:
            print(f"\n  MISMATCH:")
            print(f"    Q:        {ex['input']}")
            print(f"    Expected: {expected}")
            print(f"    Got:      {predicted}")
    except json.JSONDecodeError:
        print(f"\n  INVALID JSON output: {predicted}")

print(f"\nVal accuracy: {correct}/{len(val_data)} ({int(correct/len(val_data)*100)}%)")
print(f"\nDone. Model saved to: {args.output_dir}")
print("Next step: update exact_match.py to use the fine-tuned model.")
