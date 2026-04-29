import pandas as pd

from koala_strategy.models.advanced_fulltext_experiments import (
    drop_artifact_numeric_columns,
    payload_text,
    scrub_artifact_model_text,
)


def test_scrub_artifact_model_text_removes_page_and_bibliography_artifacts():
    text = "Page Table page figure page 054 054 055 062 063 DOI doi 2025 real method signal"

    cleaned = scrub_artifact_model_text(text)

    assert "page table" not in cleaned.lower()
    assert "page figure" not in cleaned.lower()
    assert "054" not in cleaned
    assert "055" not in cleaned
    assert "062" not in cleaned
    assert "063" not in cleaned
    assert "doi" not in cleaned.lower()
    assert "2025" not in cleaned
    assert "real method signal" in cleaned


def test_payload_text_can_apply_artifact_scrubbing_after_safe_sanitization():
    payload = {
        "title": "A Method",
        "abstract": "A clean abstract",
        "sections": {
            "introduction": "Page Figure 2025 DOI strong result",
            "results": "page 054 054 055 robust comparison",
        },
        "table_evidence": [{"caption_or_context": "Page Table 2025 doi metric"}],
    }

    cleaned = payload_text(payload, "sections_safe", drop_artifact_features=True)

    assert "page figure" not in cleaned.lower()
    assert "page table" not in cleaned.lower()
    assert "054" not in cleaned
    assert "2025" not in cleaned
    assert "doi" not in cleaned.lower()
    assert "strong result" in cleaned
    assert "robust comparison" in cleaned


def test_drop_artifact_numeric_columns_removes_parser_warning_count():
    df = pd.DataFrame(
        {
            "base_p_accept": [0.4],
            "pdf_parser_warning_count": [3.0],
            "pdf_num_tokens_fulltext": [1200.0],
        }
    )

    filtered = drop_artifact_numeric_columns(df)

    assert list(filtered.columns) == ["base_p_accept", "pdf_num_tokens_fulltext"]
