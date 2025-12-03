# Structured-ASIC
## SA Knob Exploration (Task 1.D)

To evaluate how simulated annealing parameters affect placement quality and runtime, a structured two-phase sweep was performed using the **6502** design.

---

### Phase 1 – Sweeping Cooling Rate (α) and Moves per Temperature (N)

In the first sweep, we varied the main cost–runtime control parameters:

- **Cooling Rate (α):** {0.85, 0.92, 0.97}  
- **Moves per Temperature (N):** {100, 200, 400}  
- **Initial Temperature (T₀):** 50.0 (held constant)  
- **Refine Probability (P_refine):** 0.7 (held constant)

Each configuration was run **three times with different random seeds**, starting from the same greedy initial placement. The resulting plot (`sa_knob_analysis.png`) shows:

- **Runtime (s)** on the X-axis  
- **Final HPWL (µm)** on the Y-axis  
- Pareto frontier highlighted

#### Observations

- The frontier was smooth, with **no single dramatic “knee”**, suggesting:
  - Increasing annealing effort (higher N or α) steadily improves HPWL  
  - But also increases runtime proportionally  
- **Fastest configuration:**  
  - α = 0.85, N = 100  
  - Runtime ≈ 100–110 s  
  - HPWL ≈ 427k µm  
  - Good for debugging / early development
- **Best HPWL observed:**  
  - α = 0.97, N = 400  
  - HPWL ≈ 214k–217k µm  
  - Runtime ≈ 2,000–4,000 s  
  - Best solution quality, highest effort
- **Best balance (used for Phase 2):**
  - α = 0.92, N = 200  
  - Strong improvement over fast settings  
  - Runtime manageable (few hundred seconds)

Based on this, **α = 0.92 and N = 200** were selected as the baseline for the second stage of testing.

---

### Phase 2 – Sweeping Initial Temperature (T₀) and Move Selection Bias

With α and N fixed, the second sweep explored search-dynamics parameters:

- **Initial Temperature (T₀):** {25, 50, 100, 200}  
- **Refine Probability (P_refine):** {0.25, 0.50, 0.70, 0.85}  
  - (**P_explore = 1 – P_refine**)  

Again, each configuration was run three times with independent random seeds.

#### Observations

- **Higher initial temperature (T₀)** improves early global exploration.
- **Balanced refine/explore ratios (P_refine ≈ 0.5)** give strong results without excessive runtime.
- **Very high refine probabilities (≥0.85)** converge quickly but risk getting trapped early.
- **Very low refine probabilities (≤0.25)** explore widely but are slower.

---

### Final Recommended Default SA Settings

From both sweeps, the following configuration provides the **best tradeoff between runtime and placement quality**:

Initial Temperature (T₀) = 100
Cooling Rate (α) = 0.92
Moves per Temp (N) = 200
Refine Probability = 0.50


This setting:

- Produces solutions near the Pareto frontier  
- Runs comfortably within reasonable time budgets  
- Is recommended as the standard configuration for normal Structured-ASIC placement

---

### Useful Operating Modes

| Mode | α | N | T₀ | P_refine | Runtime | HPWL | Notes |
|---|---|---|---|---|---|---|---|
| Fast (debug) | 0.85 | 100 | 50 | 0.7 | ~100 s | ~427k | Quick runtime, weakest QoR |
| **Recommended** | **0.92** | **200** | **100** | **0.5** | Few hundred s | ~300–340k | Strong QoR, reasonable runtime |
| Maximum QoR | 0.97 | 400 | 200 | 0.25 | 2,000–4,000 s | ~215k | Best quality, longest runtime |

---

### Overall Conclusion

Across all experiments, annealing effort scales smoothly with solution quality, and no single sharp “knee” dominates the frontier. However, **α = 0.92, N = 200, T₀ = 100, and P_refine = 0.50** consistently offer the best balance of placement quality and runtime, making them the recommended default SA configuration for this flow.

## Phase 3 — ECO, CTS and Verilog netlist generation

Recent work focused on completing the Phase‑3 ECO flow and ensuring the final Verilog netlist uses the fabric placement names so the netlist can be used directly against the physical fabric.

- Added a renaming utility: `tools/rename.py` — maps logical instance names to fabric cell names using the placement `.map` file.
- Integrated the renamer into the end-to-end generator: `eco_generator.py` now runs the renamer after producing `build/<design>/<design>_final.v` so the file contains fabric names by default.
- Improved CTS visualization and robustness: `visualization/cts_overlay.py` now falls back to fabric DB and clock-tree coordinates if a placement entry is missing; the CTS overlay is written to `build/<design>/<design>_cts_tree.png`.
- Added a simple validator script (`tools/validate_cts_scale.py`) to check placement and CTS coordinates against die bounds (units in µm).
- Organized scripts: analysis helpers moved to `scripts/analysis/` and visualization helpers consolidated under `visualization/`.

Build outputs (examples):
- `build/<design>/<design>_final.v` — final Verilog netlist (fabric‑named instances)
- `build/<design>/<design>_cts_tree.png` — CTS overlay visualization
- `build/<design>/eco_report.txt` — ECO summary report

Next recommended steps:
- Keep the renamer integrated in the ECO flow (already applied). Optionally add a small CI check that verifies no logical placement keys remain in the final `.v`.
- If you want to retain old experiment outputs, consider archiving `build/experiments/` before removing; otherwise the repo now only keeps the production `build/<design>/` outputs.
