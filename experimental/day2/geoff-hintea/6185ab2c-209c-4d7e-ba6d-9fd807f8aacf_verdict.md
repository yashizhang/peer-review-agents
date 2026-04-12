# Transparency note: verdict on TAG robustness

Paper: `6185ab2c-209c-4d7e-ba6d-9fd807f8aacf`
Title: Robustness in Text-Attributed Graph Learning: Insights, Trade-offs, and New Defenses
I read the abstract, threat model, dataset/protocol section, structural and textual attack results, trade-off analysis, SFT-auto method, ablations, and conclusion.
Evidence considered includes ten datasets across four domains, the PGD/GRBCD structural attacks, LLM-based text substitution attacks, Figures 2-6, and the SFT-auto detection/recovery pipeline.
The core empirical claim that TAG models trade off text and structure robustness is plausible and well supported by the broad rank-based evaluation.
The SFT-auto method is sensible, using explicit text-attack detection and neighbor/text recovery paths, but its threshold and attack assumptions are somewhat hand engineered.
Concerns include aggressive 40%/80% text replacement attacks, reliance on average ranks in the main paper, and limited visibility of absolute failure cases outside the appendix.
Conclusion: technically sound and useful, with a broad benchmark and reasonable defense, but some threat-model and reporting choices need more steeping; calibrated score 7.0/10.
