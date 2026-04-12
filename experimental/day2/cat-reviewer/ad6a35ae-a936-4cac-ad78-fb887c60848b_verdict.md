### Summary
This paper presents RobustSpring, a dataset and benchmark for evaluating the robustness of dense matching models (optical flow, scene flow, stereo) against 20 synthetic image corruptions.

### Claim-Evidence Scope Analysis
The authors claim this is a comprehensive benchmark for evaluating robustness. The evidence supports the evaluation on synthetic corruptions at a single severity level. The claim that this translates to real-world robustness is weakly supported by a limited experiment on noisy KITTI data.

### Missing Experiments and Analyses
- **Essential**: The paper evaluates pre-trained models but completely omits experiments on whether training/fine-tuning with these corruptions improves robustness or degrades clean accuracy. This is the obvious next question for any robustness benchmark.
- **Expected**: Evaluating across multiple severity levels. The authors explicitly limit to one severity due to data size, but this hides whether models fail gracefully or catastrophically as conditions worsen.
- **Helpful**: A more thorough evaluation on real-world corruptions beyond just the top 10% noisiest KITTI frames.

### Hidden Assumptions
- The primary assumption is that synthetic, procedurally generated corruptions (like "spatter" or "frost") adequately proxy the complex physical phenomena of real weather and sensor degradation.
- The evaluation assumes that ranking models on a single, tuned severity level provides a complete picture of their relative robustness.

### Limitations Section Audit
The limitations section is exactly two sentences long and only mentions the restriction to 20 corruptions due to data budget. It completely ignores fundamental limitations regarding the synthetic nature of the data, the single severity level, and the lack of training experiments. This is evasive and performative.

### Negative Results and Failure Modes
The paper does a fair job reporting where models fail (e.g., severe weather and noise), but it fails to explore *why* certain architectures are more robust than others beyond surface-level speculation.

### Scope Verdict
The claims regarding the creation of a synthetic benchmark are fully supported, but the implied scope of measuring true real-world robustness is overclaimed.

### Overall Completeness Verdict
Significant gaps. The lack of multiple severity levels and the complete absence of data augmentation/training experiments make this feel like half a paper.
