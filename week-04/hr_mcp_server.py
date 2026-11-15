"""HR MCP server: leave balances from employees.csv and search of the HR documents.

One server process serves one organization folder, which contains employees.csv and
an hr-docs/ folder of HTML/PDF policies:

    .venv/bin/python week-04/hr_mcp_server.py                                  # Hartwell (default)
    .venv/bin/python week-04/hr_mcp_server.py --org week-04/ridgeline-hotels
    .venv/bin/python week-04/hr_mcp_server.py --rebuild                        # rebuild the index
    .venv/bin/python week-04/hr_mcp_server.py --as-employee dana.whitfield@hartwellfoods.example

Tools:
  search_policy(query)        -> the 4 closest policy chunks, each with its source filename.
  get_leave_balance(email)    -> one employee's leave balances (HR staff mode, the default).
  get_leave_balance()         -> with --as-employee ADDRESS, takes no input and returns only
                                 that address's balances. The address is fixed in code, so
                                 the model cannot ask for anyone else's.

The email and balance columns are detected from each CSV's header, so organizations
with different column names (e.g. "email" vs "Email Address", "vacation_days_left"
vs "PTO Days Remaining") work without code changes.

Each organization gets its own Chroma database in week-04/.chroma/<org-folder>/,
which .gitignore keeps out of Git. The loading and chunking code is copied from
week-03/ask.py so week-04 stays self-contained. Unlike ask.py, this server makes no
Claude call: it returns the chunks and the calling model writes the answer.
"""

import argparse
import csv
import hashlib
import re
import sys
from email.utils import parseaddr
from pathlib import Path
from typing import Annotated

import chromadb
from bs4 import BeautifulSoup
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field
from pypdf import PdfReader

HERE = Path(__file__).resolve().parent          # week-04/
REPO_ROOT = HERE.parent
DEFAULT_ORG = HERE / "hartwell-hr"
DB_ROOT = HERE / ".chroma"                       # .chroma/<org-folder>/, ignored by Git
TOP_K = 4


def log(message: str) -> None:
    # stdout carries the MCP protocol, so all logging goes to stderr.
    print(message, file=sys.stderr, flush=True)


# ===========================================================================
# Policy search: loading and chunking copied from week-03/ask.py
# ===========================================================================

# all-MiniLM-L6-v2 truncates input after 256 word-pieces. A chunk is a short
# header (title/section/source) plus at most MAX_WORDS words of body text, which
# keeps the whole chunk well under that limit.
MAX_WORDS = 120
OVERLAP_WORDS = 20

BLOCK_TAGS = ["h1", "h2", "h3", "h4", "p", "li", "dt", "dd", "tr", "pre", "blockquote"]
HEADING_TAGS = {"h2", "h3", "h4"}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_site_suffix(title: str) -> str:
    # "Leave Policy | Hartwell Foods" -> "Leave Policy"
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


def docs_fingerprint(files) -> str:
    h = hashlib.sha256()
    h.update(f"{MAX_WORDS}/{OVERLAP_WORDS}".encode())
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def safe_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "-", name)[:60]


def get_collection(org_dir: Path, rebuild: bool):
    """Open (or build) this organization's Chroma collection.

    Changed from ask.py: the database and collection are named after the
    organization folder, not the docs folder, because both organizations keep
    their policies in a folder called hr-docs/. Errors raise instead of exiting.
    """
    docs_dir = org_dir / "hr-docs"
    if not docs_dir.is_dir():
        raise RuntimeError(f"No hr-docs folder in {org_dir}")
    files = sorted(p for p in docs_dir.iterdir() if p.suffix.lower() in LOADERS)
    if not files:
        raise RuntimeError(f"No HTML or PDF files found in {docs_dir}")

    name = safe_name(org_dir.name)
    db_path = DB_ROOT / name
    client = chromadb.PersistentClient(
        path=str(db_path), settings=chromadb.Settings(anonymized_telemetry=False)
    )
    fingerprint = docs_fingerprint(files)

    if not rebuild:
        try:
            existing = client.get_collection(name)
            if (existing.metadata or {}).get("fingerprint") == fingerprint:
                log(f"Using existing index {db_path.relative_to(REPO_ROOT)}/")
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
    log(f"Indexed {len(records)} chunks from {len(files)} files -> {db_path.relative_to(REPO_ROOT)}/")
    return collection


# ===========================================================================
# Employee CSV: columns detected from the header
# ===========================================================================

# A balance column mentions days and "left" or "remaining", e.g.
# "vacation_days_left" or "PTO Days Remaining".
BALANCE_HEADER = re.compile(r"days?[\s_-]+(left|remaining)\b", re.IGNORECASE)


def balance_label(header: str) -> str:
    # "vacation_days_left" -> "vacation_days"; "PTO Days Remaining" -> "pto_days"
    label = re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")
    return re.sub(r"_(left|remaining)$", "", label)


def detect_columns(csv_path: Path):
    """Return (email_column, {label: balance_column}) from the CSV header."""
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        header = [h.strip() for h in next(csv.reader(f), [])]

    email_cols = [h for h in header if "email" in h.lower()]
    if len(email_cols) != 1:
        raise RuntimeError(f"Expected exactly one email column in {csv_path}, found {email_cols or 'none'}")

    balances = {balance_label(h): h for h in header if BALANCE_HEADER.search(h)}
    if not balances:
        raise RuntimeError(f"No leave balance columns (e.g. '... days left/remaining') in {csv_path}")
    return email_cols[0], balances


def normalize_email(address: str) -> str:
    # Accepts "dana@x.example" or "Dana Whitfield <dana@x.example>".
    return parseaddr(address or "")[1].strip().lower()


class LeaveBalance(BaseModel):
    email: str = Field(description="The employee's work email address")
    balances: dict[str, int | float] = Field(description="Leave type -> days remaining, as recorded in employees.csv")


class EmployeeDirectory:
    def __init__(self, csv_path: Path):
        self.csv_path = csv_path
        self.email_col, self.balance_cols = detect_columns(csv_path)

    def lookup(self, email: str) -> LeaveBalance:
        wanted = normalize_email(email)
        # Read the file on every call so edits to employees.csv show up right away.
        with self.csv_path.open(newline="", encoding="utf-8-sig") as f:
            rows = [
                {(k or "").strip(): (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(f)
            ]
        matches = [r for r in rows if wanted and normalize_email(r.get(self.email_col, "")) == wanted]

        if len(matches) != 1:
            problem = "No employee record matches" if not matches else "More than one employee record matches"
            raise ToolError(
                f"{problem} the email address '{email}'. Do not state any leave balance. "
                "Tell the sender to contact HR directly to confirm their balance."
            )

        row = matches[0]
        balances = {}
        for label, column in self.balance_cols.items():
            try:
                value = float(row[column])
            except (KeyError, ValueError):
                raise ToolError(
                    f"The employee record for '{email}' has no valid value in '{column}'. "
                    "Do not state any leave balance. Tell the sender to contact HR directly."
                )
            balances[label] = int(value) if value.is_integer() else value
        # Only the email and balances are returned: no name, manager, or department.
        return LeaveBalance(email=wanted, balances=balances)


# ===========================================================================
# MCP server
# ===========================================================================

class PolicyChunk(BaseModel):
    rank: int
    source: str = Field(description="Policy filename to cite, e.g. leave-policy.html")
    title: str
    section: str
    similarity: float = Field(description="Cosine similarity, 0 to 1; higher is closer")
    text: str


READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


def resolve_org(path_arg: str) -> Path:
    """Accept a path relative to the current directory, the repo root, or week-04/."""
    candidate = Path(path_arg)
    if candidate.is_absolute():
        return candidate.resolve()
    for base in (Path.cwd(), REPO_ROOT, HERE):
        if (base / candidate).is_dir():
            return (base / candidate).resolve()
    return (Path.cwd() / candidate).resolve()


def build_server(org_dir: Path, rebuild: bool, as_employee: str | None) -> MCPServer:
    csv_path = org_dir / "employees.csv"
    if not csv_path.is_file():
        raise RuntimeError(f"No employees.csv in {org_dir}")

    directory = EmployeeDirectory(csv_path)
    log(f"Organization: {org_dir.name}; email column '{directory.email_col}'; "
        f"balance columns {list(directory.balance_cols.values())}")

    collection = get_collection(org_dir, rebuild)
    collection.query(query_texts=["warm up"], n_results=1)  # load the embedding model now, not on the first call

    leave_types = ", ".join(directory.balance_cols)
    server = MCPServer(
        name=f"{org_dir.name}-hr",
        instructions=(
            f"HR tools for {org_dir.name}. Use search_policy for policy questions and cite each "
            "chunk's source filename. Use get_leave_balance for leave balances. Never state a "
            "policy or a number that did not come from one of these tools."
        ),
    )

    @server.tool(
        name="search_policy",
        description=(
            f"Search {org_dir.name}'s HR policy documents. Returns the {TOP_K} closest chunks, "
            "each with its source filename, title, section, and similarity score. Cite the "
            "source filename for every policy fact you use. If no chunk answers the question, "
            "say the policies do not cover it."
        ),
        annotations=READ_ONLY,
    )
    def search_policy(
        query: Annotated[str, Field(description="A policy question or keywords, e.g. 'how many bereavement days'")],
    ) -> list[PolicyChunk]:
        if not query.strip():
            raise ToolError("The query is empty. Pass a policy question or keywords.")
        result = collection.query(query_texts=[query], n_results=TOP_K)
        return [
            PolicyChunk(
                rank=rank,
                source=meta["source"],
                title=meta["title"],
                section=meta["section"],
                similarity=round(1 - dist, 4),
                text=doc,
            )
            for rank, (doc, meta, dist) in enumerate(
                zip(result["documents"][0], result["metadatas"][0], result["distances"][0]), start=1
            )
        ]

    if as_employee:
        bound = normalize_email(as_employee)
        if not bound:
            raise RuntimeError(f"--as-employee needs an email address, got '{as_employee}'")
        log(f"Employee mode: get_leave_balance is bound to {bound}")

        # No parameters: the address is fixed here, so the model cannot request anyone else's balance.
        @server.tool(
            name="get_leave_balance",
            description=(
                "Get the leave balances of the employee who sent the email you are answering. "
                f"Takes no input. Leave types: {leave_types}. This tool cannot look up any "
                "other employee; if asked about someone else, say you can't share it."
            ),
            annotations=READ_ONLY,
        )
        def get_own_leave_balance() -> LeaveBalance:
            return directory.lookup(bound)
    else:
        @server.tool(
            name="get_leave_balance",
            description=(
                f"Look up one {org_dir.name} employee's leave balances by work email address "
                f"(for HR staff). Leave types: {leave_types}. Returns only the email and balances."
            ),
            annotations=READ_ONLY,
        )
        def get_leave_balance(
            email: Annotated[str, Field(description="The employee's work email address")],
        ) -> LeaveBalance:
            return directory.lookup(email)

    return server


def main():
    parser = argparse.ArgumentParser(description="HR MCP server: leave balances and policy search for one organization.")
    parser.add_argument("--org", default=str(DEFAULT_ORG),
                        help="Organization folder with employees.csv and hr-docs/ (default: week-04/hartwell-hr)")
    parser.add_argument("--rebuild", action="store_true",
                        help="Rebuild this organization's Chroma index before serving")
    parser.add_argument("--as-employee", metavar="ADDRESS",
                        help="Bind get_leave_balance to this sender's address; the tool then takes no input")
    args = parser.parse_args()

    org_dir = resolve_org(args.org)
    if not org_dir.is_dir():
        sys.exit(f"Organization folder not found: {args.org}")
    try:
        server = build_server(org_dir, args.rebuild, args.as_employee)
    except RuntimeError as e:
        sys.exit(f"Error: {e}")
    server.run("stdio")


if __name__ == "__main__":
    main()
