from pathlib import Path

from scripts.postprocess_marker_v3 import DEFAULT_LEAK_TERMS, postprocess_paper

from koala_strategy.paper.marker_v2 import audit_model_facing_texts


def test_default_leak_terms_do_not_flag_semantic_accept_reject_words():
    result = audit_model_facing_texts(
        {
            "model_text_v3.txt": (
                "The safety filter can reject unsafe actions, while accepted actions remain executable."
            )
        },
        leak_terms=DEFAULT_LEAK_TERMS,
    )

    assert result["ok"] is True


def test_postprocess_uses_marker_markdown_parse_report(tmp_path: Path) -> None:
    paper_id = "paper-1"
    input_root = tmp_path / "marker_raw"
    paper_root = input_root / paper_id
    markdown_dir = paper_root / "marker_markdown" / paper_id
    chunks_dir = paper_root / "marker_chunks" / paper_id
    markdown_dir.mkdir(parents=True)
    chunks_dir.mkdir(parents=True)

    (markdown_dir / "parse_report.json").write_text(
        '{"paper_id":"paper-1","page_count":1,"pdf_sha256":"abc","bytes":123}',
        encoding="utf-8",
    )
    (markdown_dir / f"{paper_id}.md").write_text(
        "# Abstract\n\nThis paper studies robust parsing.\n\n# 1 Introduction\n\nThe method is deterministic.",
        encoding="utf-8",
    )
    (chunks_dir / f"{paper_id}.json").write_text(
        """
{
  "page_info": {"0": {}},
  "blocks": [
    {"id": "/page/0/SectionHeader/1", "block_type": "SectionHeader", "html": "<h1>Abstract</h1>"},
    {"id": "/page/0/Text/2", "block_type": "Text", "html": "<p>This paper studies robust parsing.</p>"},
    {"id": "/page/0/SectionHeader/3", "block_type": "SectionHeader", "html": "<h1>1 Introduction</h1>"},
    {"id": "/page/0/Text/4", "block_type": "Text", "html": "<p>The method is deterministic.</p>"}
  ]
}
""".strip(),
        encoding="utf-8",
    )

    result = postprocess_paper(input_root, tmp_path / "processed_v3", paper_id, DEFAULT_LEAK_TERMS)

    assert result["ok"] is True
    assert (tmp_path / "processed_v3" / paper_id / "sanitization_report.json").exists()


def test_postprocess_accepts_marker_output_stem_that_differs_from_paper_id(tmp_path: Path) -> None:
    paper_id = "paper-1"
    input_root = tmp_path / "marker_raw"
    paper_root = input_root / paper_id
    markdown_dir = paper_root / "marker_markdown" / "paper"
    chunks_dir = paper_root / "marker_chunks" / "paper"
    parse_report_dir = paper_root / "marker_markdown" / paper_id
    markdown_dir.mkdir(parents=True)
    chunks_dir.mkdir(parents=True)
    parse_report_dir.mkdir(parents=True)

    (parse_report_dir / "parse_report.json").write_text(
        '{"paper_id":"paper-1","page_count":1,"pdf_sha256":"abc","bytes":123}',
        encoding="utf-8",
    )
    (markdown_dir / "paper.md").write_text(
        "# Abstract\n\nThis paper studies symlinked PDFs.\n\n# 1 Introduction\n\nThe output stem differs.",
        encoding="utf-8",
    )
    (chunks_dir / "paper.json").write_text(
        """
{
  "page_info": {"0": {}},
  "blocks": [
    {"id": "/page/0/SectionHeader/1", "block_type": "SectionHeader", "html": "<h1>Abstract</h1>"},
    {"id": "/page/0/Text/2", "block_type": "Text", "html": "<p>This paper studies symlinked PDFs.</p>"},
    {"id": "/page/0/SectionHeader/3", "block_type": "SectionHeader", "html": "<h1>1 Introduction</h1>"},
    {"id": "/page/0/Text/4", "block_type": "Text", "html": "<p>The output stem differs.</p>"}
  ]
}
""".strip(),
        encoding="utf-8",
    )

    result = postprocess_paper(input_root, tmp_path / "processed_v3", paper_id, DEFAULT_LEAK_TERMS)

    assert result["ok"] is True
    assert (tmp_path / "processed_v3" / paper_id / "paper.md").exists()
