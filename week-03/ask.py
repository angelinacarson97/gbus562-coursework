"""Ask questions about a folder of HTML and PDF documents (RAG with Chroma + Claude).

Usage:
    python ask.py "How much can I spend on dinner in a tier 1 city?"
    python ask.py --docs copperline-docs --rebuild "..."

The script:
  1. Reads every .html/.htm and .pdf file in the docs folder.
  2. Splits each document into short chunks by section heading. Each chunk records
     the document title, section heading, and source filename.
  3. Stores the chunks in a local Chroma database for that folder
     (.chroma/<folder-name>/), embedded with
     Chroma's default model (all-MiniLM-L6-v2).
  4. Retrieves the chunks closest to the question and asks Claude to answer
     using only those chunks, with numbered citations.

The Anthropic API key is read from the ANTHROPIC_API_KEY environment variable.
"""

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

import anthropic
import chromadb
from bs4 import BeautifulSoup
from pypdf import PdfReader

MODEL = "claude-sonnet-5"
HERE = Path(__file__).resolve().parent
DEFAULT_DOCS = HERE / "copperline-docs"
DB_ROOT = HERE / ".chroma"  # each docs folder gets its own database: .chroma/<folder-name>/

# all-MiniLM-L6-v2 truncates input after 256 word-pieces. A chunk is a short
# header (title/section/source) plus at most MAX_WORDS words of body text, which
# keeps the whole chunk well under that limit.
MAX_WORDS = 120
OVERLAP_WORDS = 20

BLOCK_TAGS = ["h1", "h2", "h3", "h4", "p", "li", "dt", "dd", "tr", "pre", "blockquote"]
HEADING_TAGS = {"h2", "h3", "h4"}


# ---------------------------------------------------------------------------
# Document loading: each loader returns (title, [(section, paragraph_text), ...])
# ---------------------------------------------------------------------------

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_site_suffix(title: str) -> str:
    # "Corporate Card FAQ | Copperline Manufacturing" -> "Corporate Card FAQ"
    return title.split(" | ")[0].strip()


def load_html(path: Path):
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    for tag in soup(["script", "style", "nav", "header"]):
        tag.decompose()

    title_tag = soup.find("title")
    title = strip_site_suffix(clean(title_tag.get_text())) if title_tag else ""
    h1 = soup.find("h1")
    if not title and h1:
        title = clean(h1.get_text())
    title = title or path.stem

    root = soup.find("main") or soup.body or soup
    section = "Document information"
    paragraphs = []
    for el in root.find_all(BLOCK_TAGS):
        # Skip blocks nested inside another block (e.g. <p> inside <li>) to avoid duplicates.
        if el.find_parent(BLOCK_TAGS) is not None:
            continue
        text = clean(el.get_text(" "))
        if not text or el.name == "h1":
            continue
        if el.name in HEADING_TAGS:
            section = text
            continue
        if el.name == "li":
            text = "- " + text
        paragraphs.append((section, text))
    return title, paragraphs


def looks_like_heading(line: str, prev_line: str) -> bool:
    words = line.split()
    return (
        0 < len(words) <= 8
        and line[0].isupper()
        and not line.endswith((".", ",", ":", ";", "?", "!"))
        and (prev_line == "" or prev_line.endswith((".", ":", "?", "!")))
    )


def load_pdf(path: Path):
    reader = PdfReader(str(path))
    meta_title = (reader.metadata.title if reader.metadata else None) or ""
    title = strip_site_suffix(meta_title) or path.stem

    lines = []
    for page in reader.pages:
        for line in (page.extract_text() or "").splitlines():
            line = clean(line)
            if line:
                lines.append(line)

    # Drop the running header (everything up to and including the title line)
    # and page footers like "... Page 1 of 1".
    if title in lines[:5]:
        lines = lines[lines.index(title) + 1:]
    lines = [l for l in lines if not re.search(r"\bPage \d+ of \d+$", l)]

    section = "Document information"
    paragraphs = []
    current = []
    prev = ""
    for line in lines:
        if looks_like_heading(line, prev):
            if current:
                paragraphs.append((section, " ".join(current)))
                current = []
            section = line
            prev = ""
            continue
        current.append(line)
        prev = line
    if current:
        paragraphs.append((section, " ".join(current)))
    return title, paragraphs


LOADERS = {".html": load_html, ".htm": load_html, ".pdf": load_pdf}


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def split_words(text: str):
    words = text.split()
    if len(words) <= MAX_WORDS:
        return [text]
    pieces, start = [], 0
    while start < len(words):
        pieces.append(" ".join(words[start:start + MAX_WORDS]))
        if start + MAX_WORDS >= len(words):
            break
        start += MAX_WORDS - OVERLAP_WORDS
    return pieces


def chunk_document(path: Path):
    title, paragraphs = LOADERS[path.suffix.lower()](path)

    # Group paragraphs by section, then pack them into chunks of <= MAX_WORDS words.
    sections = []
    for section, text in paragraphs:
        if sections and sections[-1][0] == section:
            sections[-1][1].append(text)
        else:
            sections.append((section, [text]))

    chunks = []
    for section, texts in sections:
        buf = []
        for text in texts:
            for piece in split_words(text):
                if buf and len(" ".join(buf + [piece]).split()) > MAX_WORDS:
                    chunks.append((section, "\n".join(buf)))
                    buf = []
                buf.append(piece)
        if buf:
            chunks.append((section, "\n".join(buf)))

    records = []
    for i, (section, body) in enumerate(chunks):
        document = f"Title: {title}\nSection: {section}\nSource: {path.name}\n\n{body}"
        records.append({
            "id": f"{path.name}::{i}",
            "document": document,
            "metadata": {"title": title, "section": section, "source": path.name, "chunk": i},
        })
    return records


# ---------------------------------------------------------------------------
# Chroma index
# ---------------------------------------------------------------------------

def docs_fingerprint(files) -> str:
    h = hashlib.sha256()
    h.update(f"{MAX_WORDS}/{OVERLAP_WORDS}".encode())
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def db_path(docs_dir: Path) -> Path:
    """Separate local Chroma database per document folder, e.g. .chroma/larkfield-docs/."""
    return DB_ROOT / re.sub(r"[^a-zA-Z0-9_-]", "-", docs_dir.name)


def get_collection(docs_dir: Path, rebuild: bool):
    files = sorted(p for p in docs_dir.iterdir() if p.suffix.lower() in LOADERS)
    if not files:
        sys.exit(f"No HTML or PDF files found in {docs_dir}")

    name = re.sub(r"[^a-zA-Z0-9_-]", "-", docs_dir.name)[:60]
    client = chromadb.PersistentClient(path=str(db_path(docs_dir)))
    fingerprint = docs_fingerprint(files)

    if not rebuild:
        try:
            existing = client.get_collection(name)
            if (existing.metadata or {}).get("fingerprint") == fingerprint:
                return existing
        except Exception:
            pass  # collection does not exist yet

    try:
        client.delete_collection(name)
    except Exception:
        pass
    collection = client.create_collection(
        name, metadata={"fingerprint": fingerprint, "hnsw:space": "cosine"}
    )

    records = [r for f in files for r in chunk_document(f)]
    collection.add(
        ids=[r["id"] for r in records],
        documents=[r["document"] for r in records],
        metadatas=[r["metadata"] for r in records],
    )
    print(f"Indexed {len(records)} chunks from {len(files)} files in {docs_dir.name}/ -> {db_path(docs_dir).relative_to(HERE)}/", file=sys.stderr)
    return collection


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You answer employee questions using only the numbered document excerpts provided.

Rules:
- Use only facts stated in the excerpts. Do not use outside knowledge or guess.
- After each claim, cite the excerpt(s) it came from with bracketed numbers, like [1] or [2][3].
- If the excerpts do not contain the answer, say that the documents provided do not cover it.
- If excerpts conflict, say so and cite both.
- Keep the answer short and direct."""


def answer(question: str, hits):
    excerpts = "\n\n".join(
        f"[{i}] {h['document']}" for i, h in enumerate(hits, start=1)
    )
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"<excerpts>\n{excerpts}\n</excerpts>\n\nQuestion: {question}",
        }],
    )
    if response.stop_reason == "refusal":
        return "The model declined to answer this question."
    return "".join(b.text for b in response.content if b.type == "text").strip()


def main():
    parser = argparse.ArgumentParser(description="Answer questions from a folder of HTML/PDF documents.")
    parser.add_argument("question", help="The question to ask")
    parser.add_argument("--docs", type=Path, default=DEFAULT_DOCS, help="Folder of documents (default: copperline-docs)")
    parser.add_argument("-k", "--top-k", type=int, default=5, help="Number of chunks to retrieve (default: 5)")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild the Chroma index from scratch")
    parser.add_argument("--show-chunks", action="store_true", help="Print the retrieved chunks")
    parser.add_argument("--save", type=Path, metavar="FILE",
                        help="Write the full run output (retrieved chunks, then ANSWER) to a UTF-8 text file")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Export it in your shell first.")

    docs_dir = args.docs if args.docs.is_absolute() else (Path.cwd() / args.docs)
    if not docs_dir.is_dir():
        docs_dir = HERE / args.docs
    if not docs_dir.is_dir():
        sys.exit(f"Docs folder not found: {args.docs}")

    collection = get_collection(docs_dir.resolve(), args.rebuild)
    result = collection.query(query_texts=[args.question], n_results=args.top_k)
    hits = [
        {"document": doc, "metadata": meta, "distance": dist}
        for doc, meta, dist in zip(result["documents"][0], result["metadatas"][0], result["distances"][0])
    ]

    # Retrieved chunks section. Collection uses cosine distance, so similarity = 1 - distance.
    chunk_lines = [
        f"Question: {args.question}",
        f"Docs folder: {docs_dir.name}",
        f"Model: {MODEL}",
        f"Top k: {args.top_k}",
        "",
        "RETRIEVED CHUNKS",
    ]
    for rank, h in enumerate(hits, start=1):
        chunk_lines += [
            "",
            f"--- Rank {rank} | similarity {1 - h['distance']:.4f} | source {h['metadata']['source']} ---",
            h["document"],
        ]

    if args.show_chunks:
        print("\n".join(chunk_lines) + "\n")

    error = None
    try:
        text = answer(args.question, hits)
    except anthropic.AuthenticationError:
        error = "Authentication failed: check that ANTHROPIC_API_KEY is valid."
    except anthropic.NotFoundError as e:
        error = f"Model or endpoint not found ({MODEL}): {e}"
    except anthropic.RateLimitError:
        error = "Rate limited by the Anthropic API. Wait a moment and try again."
    except anthropic.APIStatusError as e:
        error = f"Anthropic API error {e.status_code}: {e.message}"
    except anthropic.APIConnectionError as e:
        error = f"Could not reach the Anthropic API: {e}"

    if error:
        answer_lines = [f"ERROR: {error}"]
    else:
        cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 1 <= int(n) <= len(hits)})
        answer_lines = [text, "", "Citations:"]
        for n in cited or range(1, len(hits) + 1):
            m = hits[n - 1]["metadata"]
            answer_lines.append(f"[{n}] {m['title']} > {m['section']} ({m['source']})")
        if not cited:
            answer_lines.append("(No numbered citations in the answer; listed all retrieved chunks.)")

    print("ANSWER")
    print("\n".join(answer_lines))

    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text("\n".join(chunk_lines + ["", "ANSWER"] + answer_lines) + "\n", encoding="utf-8")
        print(f"\nSaved run output to {args.save}", file=sys.stderr)

    if error:
        sys.exit(1)


if __name__ == "__main__":
    main()
