## 1.D – Simulated Annealing Knob Exploration (Analysis)

To evaluate the impact of key Simulated Annealing (SA) parameters on placement quality and runtime, a parameter sweep was performed on the `6502` mapped design. The following SA knobs were varied:

* **Cooling Rate (`α`)**: {0.85, 0.92, 0.97}
* **Moves per Temperature (`N`)**: {100, 200, 400}
* **Initial Temperature (`T₀`)**: 50.0 (held constant)
* **Refine Exploration Probability (`P_refine`)**: 0.7 (held constant)

Each configuration was run three times with different random seeds, and the results were collected in a CSV and visualized in a 2D scatter plot of:

**Runtime (seconds) vs. Final Total HPWL (µm)**

The resulting plot, including the Pareto frontier, is shown below:

`sa_knob_analysis.png`

### Observations

#### No Single Sharp “Knee”

The Pareto curve trends smoothly, without a single dominating bent-knee point. This indicates that:

* Increasing SA effort (higher `N`, higher `α`) **consistently improves HPWL**,
* But **with steadily increasing runtime**,
* And no single configuration sharply dominates the others across both metrics.

This behavior is common in placement algorithms where optimization continues to yield improvements without dramatic saturation within the tested parameter range.

#### Fastest Runtime

The fastest configurations were:

```
α = 0.85, N = 100
```

These runs completed in ~100–110 seconds, but produced the highest HPWL (~427k µm). These represent a reasonable baseline when runtime is highly constrained, but provide the weakest solution quality.

#### Best HPWL (Highest Quality)

The lowest HPWL values were consistently achieved by:

```
α = 0.97, N = 400
```

These runs achieved HPWL in the ~214k–217k µm range, the best observed in the experiment. However, this required runtimes of ~2,000–4,000 seconds and represents the high-effort, high-quality extreme of the frontier.

#### Recommended Default Setting

Based on observing the Pareto efficiency, a strong balance point is:

```
α = 0.92, N = 200
```

This configuration:

* Reduced HPWL dramatically compared to the fastest runs,
* Required only modest additional runtime (a few hundred seconds),
* Sat near the “middle” of the frontier, representing the best tradeoff between QoR and execution time.

Therefore, we recommend:

* **Default SA setting for normal use:** `α = 0.92`, `N = 200`
* **High-effort mode:** `α = 0.97`, `N = 400`
* **Fast mode / debugging:** `α = 0.85`, `N = 100`

### Summary Table

| Mode                         | α    |   N | Runtime |      HPWL | Notes                            |
| ---------------------------- | ---- | --: | ------: | --------: | -------------------------------- |
| Fastest                      | 0.85 | 100 |   ~100s |     ~427k | Speed priority, weakest quality  |
| Best Balance *(recommended)* | 0.92 | 200 |   ~400s | ~330–340k | Strong QoR with moderate runtime |
| Best HPWL                    | 0.97 | 400 |  >2000s |     ~215k | Maximum quality, longest runtime |

### Conclusion

The experiments show a smooth Pareto tradeoff curve where additional annealing effort produces steady quality improvements. While no distinct “knee” is visible, the recommended default configuration of **α=0.92, N=200** achieves the best overall balance between runtime and final placement quality.
