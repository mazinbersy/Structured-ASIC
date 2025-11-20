### SA Knob Exploration (Task 1.D)

We swept 27 SA configurations using the 6502 design, varying cooling rate (α) and moves-per-temperature (N), and measured final HPWL and runtime.

The plot (sa_knob_analysis.png) shows a clear Pareto frontier:

- Increasing N and α improves HPWL but increases runtime.
- α = 0.85 converges very quickly but produces poor placements.
- α = 0.97 produces the best HPWL but is much slower.
- The α = 0.92 configurations are largely dominated and fall off the frontier.

The best tradeoff point (the “elbow” of the Pareto curve) appears around:

α = 0.97
N = 200
Final HPWL ≈ 246k
Runtime ≈ 1,100 s


This configuration provides **excellent quality without the extreme runtimes of α=0.97, N=400**.

For default flow settings, we recommend:


α = 0.97
moves_per_temp = 200
prob_refine = 0.7


These settings consistently yielded strong solutions on the Pareto frontier.
