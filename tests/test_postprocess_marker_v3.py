from scripts.postprocess_marker_v3 import DEFAULT_LEAK_TERMS

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

