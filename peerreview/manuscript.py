"""Normalized manuscript representation.

Accepts markdown/text/PDF and produces a structure that preserves section
headings, page numbers (where the source has them), and table/figure/appendix
references, so that agents can be required to cite manuscript locations.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .util import estimate_tokens, sha256_file, sha256_text, truncate_tokens

PAGE_MARK = "PAGE:{n}"
PAGE_MARK_RE = re.compile("PAGE:(\\d+)")

ANCHOR_RE = re.compile(
    r"\b((?:Table|Figure|Fig\.|Appendix|Section|Equation|Eq\.)\s+[A-Z]?\.?\d+(?:\.\d+)*[a-z]?)", re.I)

MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")

# Headings in text extracted from PDFs: numbered headings, or a short line that
# matches a canonical section name.
NUMBERED_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\.?\s+([A-Z][^.!?]{2,80})\s*$")
APPENDIX_HEADING_RE = re.compile(r"^\s*(Appendix|Supplementary(?:\s+\w+)?)\s*([A-Z]?\d*)[:.]?\s*(.{0,80})$", re.I)
CANONICAL_HEADINGS = (
    "abstract", "introduction", "background", "theory", "theoretical framework",
    "literature review", "hypotheses", "research design", "design", "data",
    "data and methods", "methods", "methodology", "measurement", "empirical strategy",
    "identification", "estimation", "results", "findings", "robustness",
    "robustness checks", "discussion", "conclusion", "references", "bibliography",
    "acknowledgements", "appendix", "online appendix", "supplementary materials",
)

STOPWORDS = frozenset("""
a an and are as at be been but by for from has have how i if in into is it its
of on or that the their there these this to was were what when which who will
with would you your we our not no do does did can could should may might than
then them they he she his her been being also such other more most some any
""".split())


class ExtractionError(RuntimeError):
    """Raised when no usable PDF text extractor is available."""


@dataclass
class Section:
    id: str
    doc: str
    heading: str
    level: int
    text: str
    start_page: int | None = None
    end_page: int | None = None
    anchors: list[str] = field(default_factory=list)

    @property
    def location(self) -> str:
        bits = [self.heading]
        if self.start_page:
            if self.end_page and self.end_page != self.start_page:
                bits.append(f"pp. {self.start_page}-{self.end_page}")
            else:
                bits.append(f"p. {self.start_page}")
        return f"{self.doc}: " + ", ".join(bits)

    def render(self, max_tokens: int | None = None) -> str:
        body = self.text.strip()
        if max_tokens:
            body = truncate_tokens(body, max_tokens)
        return f"### [{self.id}] {self.location}\n{body}"

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass
class Document:
    kind: str                       # manuscript | appendix | supplementary | codebook
    path: str
    sha256: str
    n_pages: int
    sections: list[Section] = field(default_factory=list)

    @property
    def anchors(self) -> list[str]:
        seen: list[str] = []
        for section in self.sections:
            for anchor in section.anchors:
                if anchor not in seen:
                    seen.append(anchor)
        return seen


@dataclass
class ManuscriptBundle:
    documents: list[Document] = field(default_factory=list)
    journal: str | None = None
    title: str | None = None

    # -------------------------------------------------------------- accessors
    @property
    def sections(self) -> list[Section]:
        return [section for doc in self.documents for section in doc.sections]

    def document(self, kind: str) -> Document | None:
        for doc in self.documents:
            if doc.kind == kind:
                return doc
        return None

    def section(self, section_id: str) -> Section | None:
        for section in self.sections:
            if section.id == section_id:
                return section
        return None

    @property
    def hashes(self) -> dict[str, str]:
        return {doc.kind: doc.sha256 for doc in self.documents}

    @property
    def inputs(self) -> list[dict[str, str | int]]:
        return [{"kind": d.kind, "path": d.path, "sha256": d.sha256, "pages": d.n_pages}
                for d in self.documents]

    # ----------------------------------------------------------------- render
    def outline(self) -> str:
        """Compact map of the manuscript: always cheap enough to keep in context."""
        lines: list[str] = []
        for doc in self.documents:
            lines.append(f"# {doc.kind.upper()} ({Path(doc.path).name}"
                         + (f", {doc.n_pages} pages" if doc.n_pages else "") + ")")
            for section in doc.sections:
                indent = "  " * max(0, section.level - 1)
                pages = ""
                if section.start_page:
                    pages = (f" [p. {section.start_page}]" if section.end_page == section.start_page
                             else f" [pp. {section.start_page}-{section.end_page}]")
                anchors = f"  {{{', '.join(section.anchors[:6])}}}" if section.anchors else ""
                lines.append(f"{indent}- [{section.id}] {section.heading}{pages}"
                             f" (~{section.tokens} tok){anchors}")
        return "\n".join(lines)

    def full_text(self, kinds: Sequence[str] | None = None, max_tokens: int | None = None) -> str:
        parts: list[str] = []
        for doc in self.documents:
            if kinds and doc.kind not in kinds:
                continue
            parts.append(f"===== {doc.kind.upper()}: {Path(doc.path).name} =====")
            for section in doc.sections:
                parts.append(section.render())
        text = "\n\n".join(parts)
        if max_tokens:
            text = truncate_tokens(text, max_tokens)
        return text

    def render_sections(self, section_ids: Iterable[str], max_tokens_each: int | None = None) -> str:
        chunks = []
        for section_id in section_ids:
            section = self.section(section_id)
            if section:
                chunks.append(section.render(max_tokens_each))
        return "\n\n".join(chunks)

    # -------------------------------------------------------------- retrieval
    def find_sections(self, query: str, *, k: int = 4, budget_tokens: int | None = None) -> list[Section]:
        """Rank sections by lexical overlap with `query`, boosting explicit anchors."""
        terms = [t for t in re.findall(r"[a-z0-9']+", query.lower())
                 if t not in STOPWORDS and len(t) > 2]
        wanted_anchors = {a.lower().replace(".", "").replace(" ", "")
                          for a in ANCHOR_RE.findall(query)}
        explicit_ids = set(re.findall(r"\[(S\d+)\]", query))
        scored: list[tuple[float, Section]] = []
        for section in self.sections:
            haystack = (section.heading + " " + section.text).lower()
            score = 0.0
            for term in set(terms):
                hits = haystack.count(term)
                if hits:
                    score += 1.0 + 0.1 * min(hits, 10)
            for anchor in section.anchors:
                if anchor.lower().replace(".", "").replace(" ", "") in wanted_anchors:
                    score += 8.0
            if section.id in explicit_ids:
                score += 20.0
            if score:
                scored.append((score, section))
        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        chosen: list[Section] = []
        used = 0
        for score, section in scored[: max(k * 3, k)]:
            if len(chosen) >= k:
                break
            if budget_tokens is not None and used + section.tokens > budget_tokens and chosen:
                continue
            chosen.append(section)
            used += section.tokens
        return chosen


# ------------------------------------------------------------------ extraction
def _reraise_control_flow(exc: BaseException) -> None:
    if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
        raise exc


def _first_line(exc: BaseException) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__


def extract_pdf_pages(path: str | Path) -> list[str]:
    """Return per-page text, trying several extractors in order of fidelity."""
    path = str(path)
    errors: list[str] = []

    try:  # pypdf
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(path)
        pages = [(page.extract_text() or "") for page in reader.pages]
        if any(p.strip() for p in pages):
            return pages
        errors.append("pypdf: no text layer")
    except BaseException as exc:  # noqa: BLE001 - a pyo3 panic is a BaseException
        _reraise_control_flow(exc)
        errors.append(f"pypdf: {_first_line(exc)}")

    try:  # pdfminer.six
        from pdfminer.high_level import extract_text  # type: ignore

        whole = extract_text(path) or ""
        if whole.strip():
            return whole.split("\f")
        errors.append("pdfminer: no text")
    except BaseException as exc:  # noqa: BLE001
        _reraise_control_flow(exc)
        errors.append(f"pdfminer: {_first_line(exc)}")

    try:  # PyMuPDF
        import fitz  # type: ignore

        with fitz.open(path) as doc:
            pages = [page.get_text() for page in doc]
        if any(p.strip() for p in pages):
            return pages
        errors.append("pymupdf: no text")
    except BaseException as exc:  # noqa: BLE001
        _reraise_control_flow(exc)
        errors.append(f"pymupdf: {_first_line(exc)}")

    if shutil.which("pdftotext"):  # poppler
        try:
            out = subprocess.run(["pdftotext", "-layout", path, "-"],
                                 capture_output=True, text=True, check=True, timeout=300)
            if out.stdout.strip():
                return out.stdout.split("\f")
            errors.append("pdftotext: no text")
        except BaseException as exc:  # noqa: BLE001
            _reraise_control_flow(exc)
            errors.append(f"pdftotext: {_first_line(exc)}")

    raise ExtractionError(
        f"Could not extract text from {path}. Tried: " + "; ".join(errors) +
        ". Install one of: `pip install pypdf` / `pip install pdfminer.six` / poppler-utils, "
        "or convert the PDF to markdown first (OCR is required for scanned PDFs).")


def _pages_to_marked_text(pages: Sequence[str]) -> str:
    return "".join(PAGE_MARK.format(n=i + 1) + "\n" + (page or "") + "\n"
                   for i, page in enumerate(pages))


def _looks_like_heading(line: str) -> tuple[bool, int, str]:
    stripped = line.strip()
    if not stripped or len(stripped) > 90:
        return False, 0, ""
    match = NUMBERED_HEADING_RE.match(stripped)
    if match:
        level = 1 + match.group(1).count(".")
        return True, level, stripped
    match = APPENDIX_HEADING_RE.match(stripped)
    if match:
        return True, 1, stripped
    bare = stripped.rstrip(":").strip()
    if bare.lower() in CANONICAL_HEADINGS:
        return True, 1, bare
    # Short, title-cased or upper-cased standalone line with no terminal period.
    if (len(bare.split()) <= 8 and not bare.endswith(('.', ',', ';'))
            and (bare.isupper() or bare.istitle()) and any(c.isalpha() for c in bare)):
        return True, 2, bare
    return False, 0, ""


def _split_markdown(text: str, doc_kind: str, id_prefix: str) -> list[Section]:
    sections: list[Section] = []
    current_heading, current_level, buffer = "Preamble", 1, []
    counter = 0

    def flush() -> None:
        nonlocal counter, buffer
        body = "\n".join(buffer).strip()
        if not body and current_heading == "Preamble":
            buffer = []
            return
        counter += 1
        sections.append(_make_section(f"{id_prefix}{counter}", doc_kind, current_heading,
                                      current_level, body))
        buffer = []

    for line in text.splitlines():
        match = MD_HEADING_RE.match(line)
        if match:
            flush()
            current_level = len(match.group(1))
            current_heading = match.group(2).strip()
        else:
            buffer.append(line)
    flush()
    return sections


def _split_plain(text: str, doc_kind: str, id_prefix: str) -> list[Section]:
    sections: list[Section] = []
    current_heading, current_level, buffer = "Front matter", 1, []
    counter = 0

    def flush() -> None:
        nonlocal counter, buffer
        body = "\n".join(buffer).strip()
        if not body:
            buffer = []
            return
        counter += 1
        sections.append(_make_section(f"{id_prefix}{counter}", doc_kind, current_heading,
                                      current_level, body))
        buffer = []

    for line in text.splitlines():
        if PAGE_MARK_RE.match(line.strip()):
            buffer.append(line)
            continue
        is_heading, level, heading = _looks_like_heading(line)
        if is_heading and len("\n".join(buffer)) > 200:
            flush()
            current_heading, current_level = heading, level
        else:
            buffer.append(line)
    flush()
    return sections


def _make_section(section_id: str, doc_kind: str, heading: str, level: int, body: str) -> Section:
    page_numbers = [int(n) for n in PAGE_MARK_RE.findall(body)]
    clean = PAGE_MARK_RE.sub(lambda m: f"[p. {m.group(1)}]", body).strip()
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    anchors: list[str] = []
    for anchor in ANCHOR_RE.findall(heading + "\n" + clean):
        normalized = re.sub(r"\s+", " ", anchor.strip().rstrip("."))
        normalized = normalized[0].upper() + normalized[1:]
        if normalized not in anchors:
            anchors.append(normalized)
    return Section(
        id=section_id, doc=doc_kind, heading=heading, level=level, text=clean,
        start_page=min(page_numbers) if page_numbers else None,
        end_page=max(page_numbers) if page_numbers else None,
        anchors=anchors,
    )


def load_document(path: str | Path, kind: str, id_prefix: str) -> Document:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{kind} not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = extract_pdf_pages(path)
        marked = _pages_to_marked_text(pages)
        sections = _split_plain(marked, kind, id_prefix)
        n_pages = len(pages)
    else:
        raw = path.read_text(encoding="utf-8", errors="replace")
        n_pages = 0
        if suffix in {".md", ".markdown"} or MD_HEADING_RE.search(raw):
            sections = _split_markdown(raw, kind, id_prefix)
        else:
            sections = _split_plain(raw, kind, id_prefix)
    if not sections:
        sections = [_make_section(f"{id_prefix}1", kind, "Document", 1,
                                  path.read_text(encoding="utf-8", errors="replace")
                                  if suffix != ".pdf" else "")]
    return Document(kind=kind, path=str(path), sha256=sha256_file(path), n_pages=n_pages,
                    sections=sections)


def load_bundle(*, manuscript: str | Path, appendix: str | Path | None = None,
                supplementary: Sequence[str | Path] = (), codebook: str | Path | None = None,
                journal: str | None = None) -> ManuscriptBundle:
    documents = [load_document(manuscript, "manuscript", "S")]
    if appendix:
        documents.append(load_document(appendix, "appendix", "A"))
    for index, extra in enumerate(supplementary, start=1):
        documents.append(load_document(extra, "supplementary", f"X{index}_"))
    if codebook:
        documents.append(load_document(codebook, "codebook", "C"))
    title = None
    first = documents[0].sections[0] if documents[0].sections else None
    if first:
        title = first.heading if first.heading not in {"Preamble", "Front matter"} else None
        if title is None:
            for line in first.text.splitlines():
                if line.strip():
                    title = line.strip()[:160]
                    break
    return ManuscriptBundle(documents=documents, journal=journal, title=title)


def bundle_fingerprint(bundle: ManuscriptBundle) -> str:
    return sha256_text("|".join(f"{d.kind}:{d.sha256}" for d in bundle.documents))
