import json
from pathlib import Path

import pytest

from koala_strategy.paper.marker_v2 import (
    audit_model_facing_texts,
    build_marker_v2_chunks,
    filter_marker_assets,
    jpeg_dimensions,
    render_model_text_v3,
    split_chunks_by_view,
)


def _minimal_jpeg(width: int, height: int) -> bytes:
    return (
        b"\xff\xd8"
        b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xc0\x00\x11\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        b"\xff\xd9"
    )


def test_build_marker_v2_chunks_skips_pre_abstract_identity_and_uses_real_pages():
    marker_payload = {
        "page_info": {"0": {}, "1": {}},
        "blocks": [
            {
                "id": "/page/0/SectionHeader/1",
                "block_type": "SectionHeader",
                "html": "<h1>Fast Proteome-Scale Protein Interaction Retrieval</h1>",
                "page": 149,
            },
            {
                "id": "/page/0/Text/2",
                "block_type": "Text",
                "html": "<p>Jianan Zhao and Zhihao Zhan</p>",
                "page": 149,
            },
            {
                "id": "/page/0/Text/3",
                "block_type": "Text",
                "html": "<p><sup>1</sup>Mila - Quebec AI Institute</p>",
                "page": 149,
            },
            {
                "id": "/page/0/SectionHeader/4",
                "block_type": "SectionHeader",
                "html": "<h1>Abstract</h1>",
                "page": 149,
            },
            {
                "id": "/page/0/Text/5",
                "block_type": "Text",
                "html": "<p>Protein-protein interactions are mediated at the residue level.</p>",
                "page": 149,
            },
            {
                "id": "/page/1/SectionHeader/1",
                "block_type": "SectionHeader",
                "html": "<h1>3 Methodology</h1>",
                "page": 382,
            },
            {
                "id": "/page/1/Text/2",
                "block_type": "Text",
                "html": "<p>RaftPPI uses a Gaussian kernel.</p>",
                "page": 382,
            },
        ],
    }

    chunks = build_marker_v2_chunks(marker_payload, paper_id="Dp1RM3gPg8", page_count=2)

    assert [chunk["page_start"] for chunk in chunks] == [1, 2]
    assert all(1 <= chunk["page_start"] <= chunk["page_end"] <= 2 for chunk in chunks)
    assert chunks[0]["section"] == "Abstract"
    assert chunks[1]["section"] == "3 Methodology"
    joined = "\n".join(chunk["text"] for chunk in chunks)
    assert "Protein-protein interactions" in joined
    assert "Gaussian kernel" in joined
    assert "Jianan Zhao" not in joined
    assert "Mila" not in joined


def test_build_marker_v2_chunks_starts_at_text_block_abstract():
    marker_payload = {
        "page_info": {"0": {}, "1": {}},
        "blocks": [
            {
                "id": "/page/0/SectionHeader/1",
                "block_type": "SectionHeader",
                "html": "<h1>Universal Adversarial Attacks</h1>",
            },
            {
                "id": "/page/0/Text/2",
                "block_type": "Text",
                "html": "<p>Hui Lu, Yi Yu, and Alex Kot</p>",
            },
            {
                "id": "/page/0/Text/3",
                "block_type": "Text",
                "html": "<p><i>Abstract</i>—Targeted attacks need universal perturbations.</p>",
            },
            {
                "id": "/page/0/SectionHeader/4",
                "block_type": "SectionHeader",
                "html": "<h1>I. INTRODUCTION</h1>",
            },
            {
                "id": "/page/1/Text/5",
                "block_type": "Text",
                "html": "<p>The method aggregates target views.</p>",
            },
        ],
    }

    chunks = build_marker_v2_chunks(marker_payload, paper_id="paper", page_count=2)

    assert [chunk["section"] for chunk in chunks] == ["Abstract", "I. INTRODUCTION"]
    joined = "\n".join(chunk["text"] for chunk in chunks)
    assert "Targeted attacks need universal perturbations" in joined
    assert "aggregates target views" in joined
    assert "Hui Lu" not in joined


def test_build_marker_v2_chunks_filters_line_number_blocks_and_rejects_bad_pages():
    marker_payload = {
        "page_info": {"0": {}},
        "blocks": [
            {"id": "/page/0/SectionHeader/1", "block_type": "SectionHeader", "html": "<h1>Abstract</h1>"},
            {"id": "/page/0/Text/2", "block_type": "Text", "html": "<p>055</p>"},
            {"id": "/page/0/Text/3", "block_type": "Text", "html": "<p>057 058</p>"},
            {"id": "/page/0/Text/4", "block_type": "Text", "html": "<p>Useful model evidence.</p>"},
        ],
    }

    chunks = build_marker_v2_chunks(marker_payload, paper_id="paper", page_count=1)

    assert [chunk["text"] for chunk in chunks] == ["Useful model evidence."]

    bad_payload = {
        "page_info": {"0": {}},
        "blocks": [
            {"id": "/page/8/Text/1", "block_type": "Text", "html": "<p>Out of range.</p>"},
        ],
    }
    with pytest.raises(ValueError, match="outside page_count"):
        build_marker_v2_chunks(bad_payload, paper_id="paper", page_count=1)


def test_build_marker_v2_chunks_skips_acknowledgment_until_safe_section():
    marker_payload = {
        "page_info": {"0": {}, "1": {}},
        "blocks": [
            {"id": "/page/0/SectionHeader/1", "block_type": "SectionHeader", "html": "<h1>Abstract</h1>"},
            {"id": "/page/0/Text/2", "block_type": "Text", "html": "<p>Model evidence.</p>"},
            {"id": "/page/0/SectionHeader/3", "block_type": "SectionHeader", "html": "<h1>Acknowledgment</h1>"},
            {
                "id": "/page/0/Text/4",
                "block_type": "Text",
                "html": "<p>Supported by Mila and Intel Corporation.</p>",
            },
            {"id": "/page/1/SectionHeader/5", "block_type": "SectionHeader", "html": "<h1>References</h1>"},
            {"id": "/page/1/Text/6", "block_type": "Text", "html": "<p>Smith et al. Useful baseline.</p>"},
        ],
    }

    chunks = build_marker_v2_chunks(marker_payload, paper_id="paper", page_count=2)

    assert [chunk["section"] for chunk in chunks] == ["Abstract", "References"]
    joined = "\n".join(chunk["text"] for chunk in chunks)
    assert "Model evidence" in joined
    assert "Smith et al." in joined
    assert "Intel Corporation" not in joined


def test_build_marker_v2_chunks_skips_llm_usage_disclosure_sections():
    marker_payload = {
        "page_info": {"0": {}, "1": {}, "2": {}},
        "blocks": [
            {"id": "/page/0/SectionHeader/1", "block_type": "SectionHeader", "html": "<h1>Abstract</h1>"},
            {"id": "/page/0/Text/2", "block_type": "Text", "html": "<p>Paper contribution.</p>"},
            {
                "id": "/page/0/SectionHeader/3",
                "block_type": "SectionHeader",
                "html": "<h1>C Use of Large Language Models</h1>",
            },
            {
                "id": "/page/0/Text/4",
                "block_type": "Text",
                "html": "<p>We used ChatGPT to polish the text.</p>",
            },
            {"id": "/page/1/SectionHeader/5", "block_type": "SectionHeader", "html": "<h1>References</h1>"},
            {"id": "/page/1/Text/6", "block_type": "Text", "html": "<p>Smith et al. Useful baseline.</p>"},
            {"id": "/page/2/SectionHeader/7", "block_type": "SectionHeader", "html": "<h1>B Visualizations</h1>"},
            {"id": "/page/2/Text/8", "block_type": "Text", "html": "<p>Appendix figure description.</p>"},
        ],
    }

    chunks = build_marker_v2_chunks(marker_payload, paper_id="paper", page_count=3)

    joined = "\n".join(chunk["text"] for chunk in chunks)
    assert "Paper contribution" in joined
    assert "Smith et al." in joined
    assert "Appendix figure description" in joined
    assert "ChatGPT" not in joined


def test_audit_model_facing_texts_checks_multiple_artifacts():
    result = audit_model_facing_texts(
        {
            "sanitized_v2.txt": "clean method evidence",
            "chunks_v2_anonymized.jsonl": json.dumps({"text": "Jianan Zhao"}),
        },
        leak_terms=["Jianan Zhao", "Mila"],
    )

    assert result["ok"] is False
    assert result["hits"]["Jianan Zhao"] == ["chunks_v2_anonymized.jsonl"]
    assert result["hits"]["Mila"] == []


def test_split_chunks_by_view_and_render_model_text_v3():
    chunks = [
        {
            "paper_id": "paper",
            "chunk_id": "paper:0000",
            "section": "Abstract",
            "page_start": 1,
            "page_end": 1,
            "type": "Text",
            "text": "Core contribution.",
        },
        {
            "paper_id": "paper",
            "chunk_id": "paper:0001",
            "section": "4 Experiments",
            "page_start": 5,
            "page_end": 5,
            "type": "TableGroup",
            "text": "Benchmark table.",
        },
        {
            "paper_id": "paper",
            "chunk_id": "paper:0002",
            "section": "References",
            "page_start": 9,
            "page_end": 9,
            "type": "Text",
            "text": "Smith et al.",
        },
        {
            "paper_id": "paper",
            "chunk_id": "paper:0003",
            "section": "A Implementation Details",
            "page_start": 10,
            "page_end": 10,
            "type": "Text",
            "text": "Appendix detail.",
        },
    ]

    views = split_chunks_by_view(chunks)
    assert [chunk["chunk_id"] for chunk in views["main_body_chunks"]] == ["paper:0000", "paper:0001"]
    assert [chunk["chunk_id"] for chunk in views["reference_chunks"]] == ["paper:0002"]
    assert [chunk["chunk_id"] for chunk in views["appendix_chunks"]] == ["paper:0003"]

    rendered = render_model_text_v3(views["main_body_chunks"])

    assert "[p. 1 | section: Abstract | type: Text]" in rendered
    assert "Core contribution." in rendered
    assert "Smith et al." not in rendered


def test_filter_marker_assets_rejects_small_or_narrow_images(tmp_path: Path):
    good = tmp_path / "good.jpeg"
    small = tmp_path / "small.jpeg"
    narrow = tmp_path / "narrow.jpeg"
    good.write_bytes(_minimal_jpeg(640, 360))
    small.write_bytes(_minimal_jpeg(170, 170))
    narrow.write_bytes(_minimal_jpeg(24, 500))

    assert jpeg_dimensions(good) == (640, 360)

    manifest = filter_marker_assets([good, small, narrow])
    by_name = {entry["filename"]: entry for entry in manifest}

    assert by_name["good.jpeg"]["keep"] is True
    assert by_name["small.jpeg"]["keep"] is False
    assert by_name["small.jpeg"]["reject_reason"] == "too_small"
    assert by_name["narrow.jpeg"]["keep"] is False
    assert by_name["narrow.jpeg"]["reject_reason"] == "extreme_aspect_ratio"
