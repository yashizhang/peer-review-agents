from __future__ import annotations

from typing import Any

import pandas as pd

from koala_strategy.discussion.discussion_features import extract_discussion_features
from koala_strategy.models.predict_paper_only import predict_paper_only
from koala_strategy.schemas import CommentRecord, PaperRecord


def update_with_discussion(
    paper: PaperRecord,
    comments: list[CommentRecord],
    artifacts: dict[str, Any],
) -> dict[str, Any]:
    prior = predict_paper_only([paper], artifacts)
    p_prior = prior["p_accept"]
    discussion = extract_discussion_features(paper.paper_id, comments)
    if "model_c" in artifacts:
        df = pd.DataFrame([discussion.features]).fillna(0.0)
        p_final = artifacts["model_c"].predict_proba(p_prior, df)
    else:
        p_final = p_prior
    return {
        "p_prior": float(p_prior[0]),
        "p_accept_final": float(p_final[0]),
        "discussion_features": discussion.features,
        "claims": discussion.extracted_claims,
    }

