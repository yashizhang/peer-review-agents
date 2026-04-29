# Spec: ICLR26 PDF Parse Cache

## Goal
Build an evidence-preserving PDF preprocessing pipeline for ICLR26 papers so the review agent reads cached Markdown, structured blocks, assets, chunks, and parse-quality reports instead of doing ad hoc PDF parsing at review time.

## Context
- Existing lightweight parsing lives in `koala_strategy/paper/pdf_cache.py`; it downloads PDFs, extracts text with PyMuPDF, sanitizes obvious leakage, and writes `data/pdf_cache/parsed/{paper_id}.json`.
- `koala_strategy/models/fulltext_evidence_model.py` and `koala_strategy/paper/parsed_payload.py` read the current parsed JSON payload for full-text evidence features and agent prompts.
- Current parsing does not produce Markdown, structured blocks, page-aware chunks, asset metadata, or parse-quality diagnostics.
- Project policy says heavy PDF parsing and corpus-scale preprocessing run on Mila, not the local workstation.
- Target tracking issue is AJZ-111, current title `PDF-to-Markdown`.
- First smoke example is paper `11072`, title `Fast Proteome-Scale Protein Interaction Retrieval via Residue-Level Factorization`.
- Local reference source for the smoke paper is `/Users/andyzhao/Projects/_arxived_research_projects/ICLR26_RaftPPI/RaftPPI/tex`.
- Local reference PDF is `/Users/andyzhao/Projects/_arxived_research_projects/ICLR26_RaftPPI/RaftPPI/tex/ICLR26 RaftPPI vCameraReady.pdf`, 18 pages, SHA256 `b1591fe190d870f0958deffa11960b324f621bc09b4a4fe939d659c6c02d8356`.
- Paper2Poster is useful only as parser inspiration: Docling/Marker, page images, figure/table extraction, captions. Its LLM poster outline, semantic compression, citation removal, and random section sampling are out of scope.

## Requirements
1. Add a parse-cache entrypoint that can parse one paper or a batch of ICLR26 records from the existing dataset loaders.
2. Generate a file inventory before parsing with at least `paper_id`, source PDF path or URL, SHA256, page count, dataset split when known, and parse-cache version.
3. Use Marker as the primary parser for Markdown and structured JSON/block output. Default settings must not use Marker LLM rewrite features.
4. Add Docling as a fallback or asset-enhancement path for failed or suspicious Marker outputs, especially page images, figure images, table images, captions, and asset dimensions.
5. Write one cache directory per paper containing:
   - `paper.md`
   - `paper.blocks.json`
   - `assets.json`
   - `chunks.jsonl`
   - `parse_report.json`
   - parser-native artifacts that are useful for debugging, such as embedded-image Markdown, referenced-image Markdown, HTML, or raw JSON when available
6. Produce section-aware chunks with stable metadata: `paper_id`, `chunk_id`, `section`, `page_start`, `page_end`, `type`, `text`, and optional asset references.
7. Preserve raw parsed text and chunk text. Do not summarize, rewrite, randomly sample, remove citations for compression, or replace original text with an LLM-generated outline.
8. Keep leakage mitigation explicit. Review-agent-facing sanitized payloads must continue to scrub venue/status, author identity, acknowledgements, DOI/page-marker/parser artifacts, and other known PDF extraction leakage without deleting the raw parse cache.
9. Emit deterministic parse-quality checks in `parse_report.json`, including at least title presence, abstract presence, section count, references boundary, page count, text length, figure/table caption counts, suspicious leakage-line counts, and parser fallback used.
10. Preserve compatibility with the current full-text feature path, either by writing a legacy-compatible sanitized JSON payload or by adapting readers to consume the new cache without breaking existing callers.
11. Add focused unit tests before implementation for inventory creation, quality-report classification, section-aware chunking, asset metadata normalization, and sanitized legacy payload generation.
12. Run a Mila smoke parse for paper `11072` after local tests pass, record exact command, remote commit or worktree state, artifact path, runtime, parser used, and quality report summary in AJZ-111.
13. Do not commit data, PDFs, parse caches, model artifacts, remote logs, tokens, or machine-local config.

## Constraints
- Default parse mode is non-LLM. `--use_llm`, `--redo_inline_math`, and similar LLM repair options are only explicit retry modes for bad cases.
- No Paper2Poster poster JSON, poster layout, PPTX generation, random section subsampling, or poster-oriented title rewriting.
- Corpus-scale parsing runs on Mila. Local runs are limited to unit tests and tiny synthetic fixtures.
- Cache roots and temporary directories on Mila must be scratch-backed, not under `~/`.
- The implementation must not assume `data/koala_iclr2026`, PDF caches, or virtualenvs are present in a clean local worktree.
- Changes should be surgical and should not refactor unrelated model or agent code.
- Public APIs should have type annotations.

## Test Plan
- First write failing unit tests with synthetic parser outputs and tiny generated PDFs where needed.
- Test inventory creation records SHA256 and page count and handles missing PDFs as a structured failure.
- Test chunking keeps chunks inside section boundaries, preserves page spans, emits deterministic `chunk_id`s, and does not drop appendix or references chunks by random sampling.
- Test parse-quality reports flag too-short text, missing abstract, missing references, low section count, and leakage-pattern hits.
- Test legacy sanitized payload generation retains compatibility with `ParsedPaperText` and strips known leakage blocks from review-agent-facing text.
- Test asset metadata normalization keeps figure/table caption, path, width, height, and aspect ratio when provided by Marker or Docling adapters.
- Run focused tests first, then `python -m pytest -q`.
- On Mila, run the 11072 smoke parse only after tests pass. If the smoke fails, inspect the command log and first parser error before retrying.

## Acceptance Criteria
- [ ] A user can run a local unit-test-only path without installing Marker or Docling.
- [ ] A user can run a Mila command for one PDF and get the required per-paper files: `paper.md`, `paper.blocks.json`, `assets.json`, `chunks.jsonl`, and `parse_report.json`.
- [ ] The 11072 smoke parse completes on Mila and records page count `18`, the source SHA256, parser used, runtime, artifact directory, and quality summary.
- [ ] `chunks.jsonl` for 11072 includes Introduction, Method, Experiments, Conclusion, References, and Appendix-derived chunks when present in the parsed PDF.
- [ ] No parser stage performs LLM summarization, semantic compression, citation deletion, or random section subsampling by default.
- [ ] The review-agent-facing sanitized payload remains compatible with the existing `ParsedPaperText` readers and does not expose author/acknowledgement/status leakage.
- [ ] AJZ-111 has a Chinese progress comment with the implementation status, Mila smoke command, artifact path, and verification results.
- [ ] Relevant local tests and type/lint checks configured by the repo pass before any commit.
