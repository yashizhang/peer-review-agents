import pandas as pd

from koala_strategy.models.advanced_fulltext_experiments import (
    drop_artifact_numeric_columns,
    fast_text_output_suffix,
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


def test_scrub_artifact_model_text_removes_numeric_tokens_and_context_indices():
    text = (
        "real method signal 0.00 3.14 25 101 102 103 10-15 "
        "Table 7 Figure 2 Appendix A.1 appendix b.2 robust conclusion"
    )

    cleaned = scrub_artifact_model_text(text)
    tokens = cleaned.lower().split()

    assert "real method signal" in cleaned
    assert "robust conclusion" in cleaned
    assert "0.00" not in tokens
    assert "3.14" not in tokens
    assert "25" not in tokens
    assert "101" not in tokens
    assert "102" not in tokens
    assert "103" not in tokens
    assert "10-15" not in tokens
    assert "7" not in tokens
    assert "2" not in tokens
    assert "a.1" not in tokens
    assert "b.2" not in tokens


def test_fast_text_output_suffix_distinguishes_strict_and_text_only_runs():
    assert (
        fast_text_output_suffix("sections_safe", drop_artifact_features=True, include_numeric=True)
        == "sections_safe_strict_artifact_scrubbed"
    )
    assert (
        fast_text_output_suffix("sections_safe", drop_artifact_features=True, include_numeric=False)
        == "sections_safe_strict_artifact_scrubbed_text_only"
    )


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
