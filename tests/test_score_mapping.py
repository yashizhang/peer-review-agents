from koala_strategy.models.score_mapping import percentile_to_koala_score, shrink_score_for_uncertainty


def test_percentile_bands_correct():
    assert percentile_to_koala_score(0.10) < 3
    assert 3 <= percentile_to_koala_score(0.50) <= 5
    assert 5 <= percentile_to_koala_score(0.80) <= 7
    assert 7 <= percentile_to_koala_score(0.95) <= 9
    assert percentile_to_koala_score(0.99) > 9


def test_uncertainty_shrinkage_and_bounds():
    assert shrink_score_for_uncertainty(9.0, 1.0) == 4.8
    assert shrink_score_for_uncertainty(9.0, 0.0) == 9.0
    for q in [0, 0.2, 0.5, 0.9, 1.0]:
        assert 0 <= percentile_to_koala_score(q) <= 10

