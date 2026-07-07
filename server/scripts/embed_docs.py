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
