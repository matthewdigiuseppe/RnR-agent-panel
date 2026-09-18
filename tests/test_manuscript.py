"""Manuscript normalization: headings, page numbers, anchors, retrieval, hashing."""
from __future__ import annotations

import pytest

from peerreview.manuscript import (ExtractionError, Section, _pages_to_marked_text, _split_plain,
                                   load_bundle, load_document)


def test_markdown_sections_headings_and_anchors(tmp_path):
    path = tmp_path / "m.md"
    path.write_text("""# A Title

## 1. Introduction
We test this in Table 3 and Figure 2.

## 2. Design
See Appendix A2 for balance.
""", encoding="utf-8")
    document = load_document(path, "manuscript", "S")
    headings = [section.heading for section in document.sections]
    assert headings == ["A Title", "1. Introduction", "2. Design"]
    assert "Table 3" in document.sections[1].anchors
    assert "Figure 2" in document.sections[1].anchors
    assert "Appendix A2" in document.sections[2].anchors
    assert document.sha256


def test_pdf_style_text_keeps_page_numbers():
    pages = ["Introduction\nThe argument begins here. " + "x " * 80,
             "Research Design\nWe use a difference-in-differences design. " + "y " * 80,
             "Results\nTable 3 reports the estimates. " + "z " * 80]
    marked = _pages_to_marked_text(pages)
    sections = _split_plain(marked, "manuscript", "S")
    assert len(sections) >= 2
    assert any(section.start_page == 1 for section in sections)
    pages_seen = {section.start_page for section in sections}
    assert pages_seen & {2, 3}
    rendered = sections[0].render()
    assert "[p. 1]" in rendered, "page markers must survive into the text agents read"


def test_location_string_is_citable():
    section = Section(id="S4", doc="manuscript", heading="5. Results", level=2,
                      text="text", start_page=18, end_page=19)
    assert section.location == "manuscript: 5. Results, pp. 18-19"


def test_retrieval_prefers_sections_matching_the_issue(tmp_path):
    path = tmp_path / "m.md"
    path.write_text("""# Title

## Theory
Attribution requires salience and clear responsibility.

## Identification
Parallel trends is assumed. Table 2 reports the two-way fixed effects estimates.

## Heterogeneity
Table 3 splits the sample by public employment share.
""", encoding="utf-8")
    bundle = load_bundle(manuscript=path)
    found = bundle.find_sections("parallel trends assumption Table 2 estimates", k=1)
    assert found[0].heading == "Identification"
    found = bundle.find_sections("mechanism heterogeneity Table 3", k=1)
    assert found[0].heading == "Heterogeneity"


def test_bundle_combines_manuscript_and_appendix(tmp_path):
    (tmp_path / "m.md").write_text("# Paper\n\n## Results\nTable 3.\n", encoding="utf-8")
    (tmp_path / "a.md").write_text("# Appendix\n\n## A1\nEvent study.\n", encoding="utf-8")
    bundle = load_bundle(manuscript=tmp_path / "m.md", appendix=tmp_path / "a.md")
    assert {doc.kind for doc in bundle.documents} == {"manuscript", "appendix"}
    assert [section.id for section in bundle.document("appendix").sections][0].startswith("A")
    outline = bundle.outline()
    assert "MANUSCRIPT" in outline and "APPENDIX" in outline
    assert bundle.title == "Paper"
    assert len(bundle.hashes) == 2


def test_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_document(tmp_path / "nope.md", "manuscript", "S")


def test_pdf_without_an_extractor_explains_what_to_install(tmp_path, monkeypatch):
    import peerreview.manuscript as manuscript_module

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really a pdf")
    monkeypatch.setattr(manuscript_module.shutil, "which", lambda name: None)
    with pytest.raises(ExtractionError, match="pip install"):
        load_document(pdf, "manuscript", "S")
