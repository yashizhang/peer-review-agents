from koala_strategy.data.output_guard import validate_public_output


def test_output_guard_blocks_future_leakage_terms():
    ok, issues = validate_public_output("This cites the meta-review and OpenReview scores.")
    assert not ok
    assert issues


def test_output_guard_allows_normal_review_language():
    ok, issues = validate_public_output("This is decision-relevant because Table 1 supports the main claim.")
    assert ok
    assert not issues
