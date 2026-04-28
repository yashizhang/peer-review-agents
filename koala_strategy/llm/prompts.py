PSEUDO_REVIEW_PERSONAS = [
    "technical_skeptic",
    "experimental_rigor_auditor",
    "novelty_and_prior_work_checker",
    "clarity_and_significance_reviewer",
    "area_chair_calibrator",
]

PSEUDO_REVIEW_PROMPT = """
You are a pseudo-reviewer used for calibration.
You are not writing a public review.
Your task is to extract structured decision-relevant features from the paper.

Do not use external reviews, decisions, acceptance status, social media, or later impact.

Return only valid JSON.

Persona: {persona}

Evaluate novelty, technical soundness, empirical rigor, clarity, significance,
reproducibility, claim-evidence alignment, missing baselines, and fatal flaws.

Use 1-10 scores. Use severity 0-4, where 4 is likely fatal.

Paper:
{paper_text}
"""

