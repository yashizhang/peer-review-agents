from koala_strategy.paper.text_sanitizer import (
    sanitize_model_text,
    sanitized_fulltext_payload,
)


def test_sanitize_model_text_removes_marker_author_header_before_abstract():
    raw = """# FAST PROTEOME-SCALE PROTEIN INTERACTION RETRIEVAL VIA RESIDUE-LEVEL FACTORIZATION

Jianan Zhao $^{1,2},$ Zhihao Zhan $^{1,2},$ Narendra Chaudhary $^3$

<sup>1</sup>Mila - Quebec AI Institute, <sup>2</sup>Universite de Montreal, <sup>3</sup>Intel Corporation

# **ABSTRACT**

Protein-protein interactions are mediated at the residue level.

# 3 METHODOLOGY

RaftPPI represents residue interactions with a Gaussian kernel.
"""

    cleaned = sanitize_model_text(raw)

    assert "FAST PROTEOME-SCALE" in cleaned
    assert "ABSTRACT" in cleaned
    assert "Protein-protein interactions" in cleaned
    assert "METHODOLOGY" in cleaned
    assert "Gaussian kernel" in cleaned
    assert "Jianan Zhao" not in cleaned
    assert "Narendra Chaudhary" not in cleaned
    assert "Mila" not in cleaned
    assert "Intel Corporation" not in cleaned


def test_sanitize_model_text_removes_acknowledgment_block_but_keeps_references():
    raw = """# 5 CONCLUSION

The retrieval system scales to proteome-wide screening.

#### ACKNOWLEDGMENT

This work was supported by Intel-MILA and Microsoft Research.

# REFERENCES

Smith et al. A useful baseline. 2024.

# APPENDIX

Additional retrieval results are provided.
"""

    cleaned = sanitize_model_text(raw)

    assert "retrieval system scales" in cleaned
    assert "REFERENCES" in cleaned
    assert "Smith et al." in cleaned
    assert "APPENDIX" in cleaned
    assert "Additional retrieval results" in cleaned
    assert "ACKNOWLEDGMENT" not in cleaned
    assert "Intel-MILA" not in cleaned
    assert "Microsoft Research" not in cleaned


def test_sanitize_model_text_removes_stray_affiliation_footnotes_and_legal_disclaimers():
    raw = """# ABSTRACT

Protein-protein interactions are mediated at the residue level.

# 1 Introduction

RaftPPI models residue-residue scores with a Gaussian kernel.

<sup>&</sup>lt;sup>4</sup>University of Texas Southwestern Medical Center, <sup>5</sup>HEC Montreal, <sup>6</sup>CIFAR AI Chair

The vector search stage keeps retrieval sublinear in the number of candidates.

For more information go to Intel, Xeon, and Intel Xeon Phi are trademarks of Intel Corporation in the U.S. and/or other countries.
"""

    cleaned = sanitize_model_text(raw)

    assert "Protein-protein interactions" in cleaned
    assert "Gaussian kernel" in cleaned
    assert "vector search stage" in cleaned
    assert "University of Texas" not in cleaned
    assert "HEC Montreal" not in cleaned
    assert "CIFAR AI Chair" not in cleaned
    assert "Intel Corporation" not in cleaned
    assert "trademarks" not in cleaned


def test_sanitized_fulltext_payload_uses_anonymous_full_text():
    payload = {
        "title": "Fast Proteome-Scale Protein Interaction Retrieval via Residue-Level Factorization",
        "abstract": "Protein-protein interactions are mediated at the residue level.",
        "full_text": """# FAST PROTEOME-SCALE PROTEIN INTERACTION RETRIEVAL VIA RESIDUE-LEVEL FACTORIZATION

Jianan Zhao $^{1,2}$ and Zhihao Zhan $^{1,2}$

<sup>1</sup>Mila - Quebec AI Institute, <sup>2</sup>Universite de Montreal

# ABSTRACT

Protein-protein interactions are mediated at the residue level.

# 4 EXPERIMENTS

RaftPPI retrieves candidates in 5.7 minutes.
""",
        "sections": {},
        "table_evidence": [],
        "figure_captions": [],
    }

    cleaned = sanitized_fulltext_payload(payload, "full")

    assert "Fast Proteome-Scale" in cleaned
    assert "Protein-protein interactions" in cleaned
    assert "RaftPPI retrieves candidates in 5.7 minutes" in cleaned
    assert "Jianan Zhao" not in cleaned
    assert "Zhihao Zhan" not in cleaned
    assert "Mila" not in cleaned


def test_sanitize_model_text_removes_pdf_line_number_pollution():
    raw = """# ABSTRACT

Protein-protein interactions are mediated at the residue level.

055
057 058

The evidence-bearing line should remain.
"""

    cleaned = sanitize_model_text(raw)

    assert "Protein-protein interactions" in cleaned
    assert "The evidence-bearing line should remain" in cleaned
    assert "055" not in cleaned
    assert "057 058" not in cleaned


def test_sanitize_model_text_removes_urls_without_word_boundary():
    raw = """# ABSTRACT

The method is described in the paper.

Ihttps://www.intel.com/content/www/us/en/developer/articles/technical/stable-diffusion-inference-on-intel-data-center-gpu-max-series.html
www.example.org/artifact
"""

    cleaned = sanitize_model_text(raw)

    assert "The method is described" in cleaned
    assert "https://" not in cleaned
    assert "www.example.org" not in cleaned
