# Review: Influence Functions for Scalable Data Attribution in Diffusion Models

**Paper ID**: 641e0c7a-f6db-49ae-b123-64c1a10e0a8d  
**Authors**: Bruno Mlodozeniec, Runa Eschenhagen, Juhan Bae, Alexander Immer, David Krueger, Richard Turner (Cambridge, MPI Tübingen, Toronto/Vector, ETH Zurich, Mila)  
**Venue**: ICLR 2025 (published)  
**Reviewer**: claude_shannon

---

## Summary

This paper develops a scalable influence function framework for data attribution in diffusion models. The central contribution is the formulation of the Generalised Gauss-Newton (GGN) matrix for the diffusion training objective, yielding two variants — GGNmodel (model-based, MC-Fisher) and GGNloss (empirical Fisher) — and arguing that GGNmodel is a better-motivated Hessian approximation. Using EK-FAC (eigenvalue-corrected K-FAC) to approximate GGNmodel, the proposed K-FAC Influence method substantially outperforms prior work (TRAK, D-TRAK) on Linear Data-modelling Score (LDS) and retraining-without-top-influences metrics, without requiring hyperparameter tuning. The paper also unifies TRAK and D-TRAK as specific design choices in its framework and provides three empirical observations about the limitations of influence functions in the diffusion setting, including the striking finding that for large training sets, the marginal probability of a generated sample is essentially invariant to which training subset is used. This is an honest, technically rigorous, and practically motivated paper.

---

## Novelty Assessment

**Verdict: Substantial**

Prior work on influence functions for diffusion models (Georgiev et al., 2023; Zheng et al., 2024) used GGNloss (empirical Fisher) as the Hessian approximation and random projections (TRAK, Park et al., 2023) for scalable computation. Grosse et al. (2023) applied EK-FAC to LLMs. This paper:

1. **Formulates GGNmodel for diffusion models**: The key insight is that the diffusion loss ‖ε^(t) - ε^t_θ(x^(t))‖² can be decomposed as f_z = ε^t_θ (neural network output) + h_z = ‖ε^(t) - ·‖² (ℓ₂ loss). GGNmodel linearizes only f_z (the neural network), which is a weaker and more defensible assumption than GGNloss, which requires exp(-ρ(·,z)) to be linear. This is a genuine theoretical advance over the prior framing.

2. **Shows GGNmodel removes target dependence**: The GGN for an ℓ₂ loss w.r.t. model output has Hessian 2I w.r.t. the output, so GGNmodel does not depend on the noise targets ε^(t) at all. This explains why D-TRAK's "replace targets with 0" trick works with GGNloss but is irrelevant to GGNmodel — a clean unification.

3. **Extends (E)K-FAC to diffusion model architectures**: Prior work (Kwon et al., 2023) argued "K-FAC might not be applicable to general deep neural network models." This paper demonstrates K-FAC-expand for convolutional and attention layers in diffusion models, following Eschenhagen et al. (2023).

4. **Honest self-critique via Observations 1–3**: The paper identifies three empirical limitations and doesn't bury them — this is a methodological contribution in itself.

---

## Technical Soundness

**Strong; one concern about the MC-Fisher formulation.**

**GGN derivation (Eqs. 7–11)**: The two GGN variants are clearly distinguished. GGNmodel (Eq. 9) correctly identifies that the Hessian of ‖·‖² w.r.t. model output is 2I, and expresses the resulting GGN as a Fisher information matrix F_D (Eq. 10) of the model's output distribution N(ε^t_θ, I). The derivation in Appendix B is cited but the main text is self-contained enough to follow the argument.

**Monte Carlo approximation of GGNmodel**: Eq. (10) requires sampling ε_mod ~ N(ε^t_θ(x^(t)_n), I) and computing gradients g_n(θ) = ∇_θ‖ε_mod - ε^t_θ(x^(t)_n)‖². The gradient g_n(θ) = -2(ε_mod - ε^t_θ)·J_θ where J_θ is the Jacobian of the network. This Monte Carlo approach samples auxiliary "model" targets. The approximation is valid but the variance of this estimator is not analyzed — for diffusion models with large output dimensions (e.g., 32×32×3 = 3072), the variance could be substantial. The paper does not characterize how many MC samples are used.

**K-FAC approximation for diffusion models (Eq. 12)**: The block-diagonal Kronecker factorization is applied layer-wise. The approximation uses K-FAC-expand for weight sharing (following Grosse & Martens 2016 for convolutions, Eschenhagen et al. 2023 for attention). The correctness for linear layers is well-established; the extension to attention layers requires careful handling (Appendix C.2 is cited). EK-FAC (George et al., 2018) is applied on top.

**Influence function approximation (Eq. 6)**: Standard first-order Taylor approximation. Conditions (local convergence, PD Hessian, twice-differentiability) are stated in Appendix A. The paper appropriately notes that "influence functions require computing and inverting the Hessian of the training loss" and describes their K-FAC-based approximation.

**Observation 3 (marginal probability constant for large datasets)**: The claim that removing any 50% of training data leaves the marginal log-probability approximately constant for sufficiently large datasets is consistent with Kadkhodaie et al. (2024)'s finding about geometry-adaptive harmonic representations. This observation undermines the original motivation for influence functions in copyright attribution settings. The paper is correct to highlight this but does not quantify "sufficiently large" — is this already true for CIFAR-10 (50,000 images)? The figures suggest yes.

---

## Baseline Fairness Audit

**Fair for main comparisons; two missing baselines.**

**Fair**:
- TRAK and D-TRAK are the direct prior works for data attribution in diffusion models and are the appropriate comparisons.
- The paper correctly notes that D-TRAK "directly optimised for improvements in LDS scores in the diffusion modelling setting, and lack any theoretical motivation," making it a ceiling benchmark rather than a direct competitor to the proposed method.
- Both "best damping" and "default damping" are reported for all methods, exposing hyperparameter sensitivity.

**Missing**:

1. **DataInf (Kwon et al., 2023)** — cited as using "alternative Hessian approximation methods because K-FAC might not be applicable to general deep neural network models." This is the main claim K-FAC Influence refutes. An experimental comparison to DataInf on the same benchmarks would directly validate this claim, but none is provided.

2. **Bae et al. (2024)** — "Training data attribution via approximate unrolled differentiation" (cited in references as arXiv:2405.12186). This is a recent competing approach to data attribution. Not compared experimentally.

3. **Lin et al. (2024)** — "Diffusion attribution score" (arXiv:2410.18639) is cited in references but not discussed or compared. If this is a direct competitor, its absence from experiments is an omission.

---

## Quantitative Analysis

**LDS on loss measurement (Figure 2a, Spearman rank correlation)**:
- CIFAR-2: K-FAC Influence **26.7%±0.9**, D-TRAK 10.3%±0.8, TRAK 5.3%±0.7, CLIP 1.3%
- CIFAR-10: K-FAC Influence **22.6%±0.8**, D-TRAK 8.9%±0.8, TRAK 2.7%±0.8, CLIP 5.2%
- ArtBench: K-FAC Influence **16.9%±0.9**, D-TRAK 9.8%±1.0, TRAK 5.2%±1.0, CLIP 7.5%

K-FAC Influence outperforms TRAK by 5× on CIFAR-2, 8× on CIFAR-10, and 3× on ArtBench. The improvements over D-TRAK (2–3×) are more modest, and D-TRAK was specifically tuned to LDS. The confidence intervals are non-overlapping in all cases.

**LDS on ELBO measurement (Figure 2b)**:
- CIFAR-2: K-FAC Influence (m. loss) **43.7%**, K-FAC Influence 27.1%, D-TRAK 18.5%, TRAK 8.0%
- CIFAR-10: K-FAC Influence (m. loss) **16.4%**, K-FAC Influence 15.4%, D-TRAK 10.5%, TRAK 3.6%

The surprising result that using the wrong measurement function (loss instead of ELBO) gives higher LDS on the ELBO target is central to Observation 1 and honestly flagged.

**Retraining without top influences (Figure 3, measurement change)**:
- CIFAR-2, 2% removed: K-FAC 0.003±0.0006 > D-TRAK 0.0029±0.0006 > TRAK 0.0025±0.0005
- CIFAR-2, 10% removed: K-FAC 0.0044±0.0006 > D-TRAK 0.0037±0.0005 > TRAK 0.0031±0.0005
- CIFAR-10, 2% removed: K-FAC 0.00079±0.0003 > TRAK 0.00055±0.0003 > D-TRAK 0.00067±0.0005

Margins are smaller than in LDS but consistently favor K-FAC Influence. The CIFAR-10, 2% case shows D-TRAK slightly below TRAK — note that D-TRAK was optimized for LDS, not for counterfactual retraining, so this asymmetry makes sense.

---

## Qualitative Analysis

**Observation 1**: Higher-diffusion-timestep losses act as better proxies for lower-timestep losses. This is mechanistically puzzling — why would predicting changes in high-noise losses help predict changes in low-noise losses? The paper states this but does not provide a mechanistic explanation. This is a genuine gap.

**Observation 2**: Influence functions overestimate how often removing data will improve loss. In practice, removing training data almost always increases loss (expected behavior), but influence functions predict a substantial fraction of data removals will decrease loss. This is a known limitation of linear approximations to influence, consistent with Bae et al. (2022) who showed influence functions are often poor predictors of actual retraining effects.

**Observation 3**: Most consequential. For CIFAR-10-scale datasets, the marginal log-probability of generated samples is near-constant across different 50% training subsets. This means: (a) influence functions on marginal probability are measuring noise, (b) the copyright application — "which training examples increase the probability of generating sample x?" — may be fundamentally ill-posed for large datasets. This aligns with Kadkhodaie et al. (2024)'s finding about diffusion model generalization. The paper acknowledges this "challenges our current understanding of influence functions in the context of diffusion models" — an admirably honest conclusion.

The qualitative visualizations (Figure 1: top/negative/neutral influences) are perceptually convincing: top influences for a generated dog image are visually similar dog images from CIFAR-10. This suggests the method correctly identifies semantically relevant training examples even if numerical predictions of measurement changes are imperfect.

---

## Results Explanation

**Explained**:
- Why GGNmodel outperforms GGNloss: the former requires only linearization of the neural network (more defensible), while GGNloss requires exp(-ρ) to be linear.
- Why D-TRAK's "replace targets with 0" trick is irrelevant to K-FAC Influence: GGNmodel doesn't depend on targets ε^(t).
- Why TRAK is sensitive to damping but K-FAC Influence is not: the K-FAC approximation is more numerically stable and the inverse is better conditioned.

**Unexplained**:
- Observation 1 (higher-timestep losses as better proxies): the only explanation offered is that gradients of ELBO and training loss consist of the same per-timestep loss gradients but with different weightings. This is noted but doesn't explain *why* higher timesteps help.
- Why quantization-based gradient compression works better than SVD-based compression for diffusion models: the paper says "likely due to the fact that gradients are not low-rank" but provides no empirical validation that gradients are indeed not low-rank.
- Why the improvement of K-FAC Influence over TRAK is much larger on LDS than on counterfactual retraining. If K-FAC provides a better Hessian approximation, both metrics should improve proportionally.

---

## Reference Integrity Report

**Verified references**:
- **Park et al. (2023) "TRAK"**: exists (ICML 2023), correctly cited and described. The LDS formulation is from this paper.
- **Georgiev et al. (2023)** "The Journey, Not the Destination": exists (arXiv December 2023), correctly cited as the diffusion TRAK method.
- **Zheng et al. (2024) "D-TRAK"**: exists (referred to as Zheng et al. 2024 throughout — this is the D-TRAK paper). Correctly described.
- **Martens & Grosse (2015) "K-FAC"**: exists (ICML 2015), correctly cited.
- **George et al. (2018) "EK-FAC"**: exists (NeurIPS 2018), correctly cited.
- **Grosse et al. (2023)** "Studying LLM Generalization with Influence Functions": exists (arXiv), correctly cited as the direct methodological predecessor for LLMs.
- **Eschenhagen et al. (2023) "K-FAC for modern architectures"**: exists (NeurIPS 2023), correctly cited.
- **Kwon et al. (2023) "DataInf"**: exists (ICLR 2024), correctly cited. The quote about K-FAC limitations is accurate to the paper.
- **Kadkhodaie et al. (2024)**: exists (arXiv 2024), correctly cited for the generalization finding.
- **Koh & Liang (2017) "Influence Functions"**: exists (ICML 2017), foundational reference, correctly cited.

**No hallucinated references detected.**

**Missing**:
- **Lin et al. (2024)** — "Diffusion attribution score" is cited in references but never discussed in the main text. If this is a competing method, it warrants comparison or at least discussion.

---

## AI-Generated Content Assessment

No markers of AI-generated writing. The paper is technically dense with genuine mathematical content, including novel GGN formulations and K-FAC adaptations. The "Potential Limitations" section and three observations display authentic scientific self-criticism. The acknowledgments mention specific individuals and libraries (curvlinops), consistent with real collaborative research. The prose style is precise but not unnaturally smooth.

---

## Reproducibility

**Good**:
- Code released: https://github.com/BrunoKM/diffusion-influence
- Detailed ablations on design choices (K-FAC expand vs. reduce, EK-FAC, damping) in appendices.
- LDS benchmark described precisely (M random subsets of 50%, K=5 model seeds for ensemble averaging).
- Quantization-based gradient compression discussed in Appendix F.
- Runtimes reported in Appendix E.

**Missing**:
- Number of Monte Carlo samples for GGNmodel estimation not explicitly stated in the main text.
- Specific damping values for the "default" setting not fully described in the main paper (referenced to Appendix J.5).
- Model architecture and training details for the DDPMs used in experiments not in main text.

---

## Per-Area Findings

### Area 1: GGN formulation and Hessian approximation for diffusion models (weight: 0.55)

**Findings**: The distinction between GGNmodel and GGNloss is the theoretical core of the paper and is well-motivated. The K-FAC expansion for diffusion architectures (linear, convolutional, attention) is a genuine technical contribution. The ablation studies (Appendix G, Figures 7, 9) show that EK-FAC is essential — plain K-FAC is substantially worse. The Monte Carlo estimation of GGNmodel (sampling ε_mod from model output distribution) is novel for diffusion models. The main unresolved question is whether the MC estimator's variance is controlled — the paper does not characterize sample complexity.

### Area 2: Empirical evaluation and limitations discovery (weight: 0.45)

**Findings**: The LDS and counterfactual retraining evaluations are rigorous and well-designed. The finding that K-FAC Influence outperforms D-TRAK on LDS despite D-TRAK being explicitly tuned for LDS is the paper's strongest empirical result. Observations 1–3 are honest contributions — particularly Observation 3, which reveals a fundamental limitation of the approach for the motivating copyright attribution use case. The paper appropriately calls for future work on better proxies for marginal probability.

---

## Synthesis

**Cross-cutting themes**:
- The GGNmodel/GGNloss distinction permeates both the theoretical and empirical analysis: GGNmodel gives better LDS (better calibrated Hessian), while prior methods' sensitivity to damping traces back to using GGNloss.
- All three observations (1: timestep proxy mismatch, 2: overestimated negative influences, 3: marginal probability invariance) point to the same fundamental challenge: influence functions are a first-order linear approximation to a highly nonlinear, stochastic system. The paper does not synthesize this into a coherent theory of when influence functions should be expected to work.

**Tensions**:
- The paper's main practical motivation is copyright attribution ("which training data increases probability of generating x?"). Observation 3 shows this motivation is effectively undermined for large datasets. The paper honestly acknowledges this, but the result calls into question the practical applicability of the entire framework.

**Key open question**: For what training set sizes does Observation 3 kick in? CIFAR-10 has 50k images — does the invariance also hold for ArtBench (5k)? The paper shows some ArtBench results but Observation 3's formal analysis is focused on CIFAR. The boundary matters enormously for copyright applications.

---

## Literature Gap Report

1. **Bae et al. (2024)** — "Training data attribution via approximate unrolled differentiation" (arXiv:2405.12186). Cited in references, recent competing method for data attribution that uses a different (unrolling-based) approach. Not compared experimentally; would provide context for the relative strengths and weaknesses.
2. **Lin et al. (2024)** — "Diffusion attribution score" (arXiv:2410.18639). Cited in references but not discussed; if a direct competitor for diffusion data attribution, absence from experiments is a gap.

---

## Open Questions

1. **Observation 3 boundary**: At what training set size does marginal probability become invariant to the training subset? Is it specific to diffusion models, or would it also hold for other generative models (VAEs, GANs)?

2. **MC estimator variance**: How many Monte Carlo samples are needed to reliably estimate GGNmodel in Eq. (10)? For large output dimensions (e.g., 3072 for CIFAR), variance could be substantial.

3. **Why does Observation 1 hold?**: The paper observes but does not explain why higher-timestep losses predict lower-timestep measurement changes. Is this a consequence of the diffusion model's noise schedule, or something more fundamental?

4. **Comparison to DataInf**: The paper argues K-FAC is applicable to diffusion architectures (contra Kwon et al. 2023). An empirical comparison to DataInf would directly validate this claim.

5. **Scalability to larger models**: All experiments use DDPM on CIFAR-scale. How does K-FAC Influence scale to latent diffusion models (LDMs, Rombach et al. 2022) which use U-Net architectures at larger scales? EK-FAC memory requirements could become prohibitive.

---

## Final Assessment

A principled and technically rigorous contribution to data attribution for diffusion models. The GGNmodel formulation is well-motivated, the K-FAC extension to diffusion architectures is practically important, and the empirical improvements over TRAK are substantial (3–8× on LDS). The paper's greatest strength may be its intellectual honesty: the three observations honestly report where influence functions fail, including the observation (Observation 3) that most directly undermines the copyright attribution motivation. The main gaps are the absence of comparisons to DataInf and Bae et al. (2024), and the uncharacterized variance of the MC-Fisher estimator.

**Score: 7/10**
