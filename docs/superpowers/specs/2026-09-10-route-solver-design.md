# Route solver: audit of the ant colony, algorithm survey, and selectable solvers

Written 2026-09-10. Scope: `core/apca.py` (the optimizer behind Plan → Results), the settings that
drive it, and a solver drop-down so organizers can pick the algorithm.

## 1. Audit of the current ant colony (APCA)

`core/apca.py` implements "Ant Path-oriented Carpooling Allocation" (Huang, Jiau & Liu, IEEE
Systems Journal 2019) simplified to many origins → one destination. Evidence below comes from
`scratchpad/bench_aco.py` (brute-force comparison on 2-driver / 6-passenger instances, seeds 0–7)
and a hand-built clustered instance. All numbers at the default 30 ants × 150 iterations.

### 1.1 Findings, most severe first

1. **The construction step cannot produce unbalanced splits.** `_construct_solution` hands out
   passengers strictly round-robin (one per driver per round), so every ant yields the most
   balanced split capacity allows. On a clustered instance (five passengers beside driver A, one
   beside driver B, both cars seat five) the ant returns a 3+3 plan at 138 km; the optimum 5+1 plan
   is 82 km (+68 %). The random 6-passenger instances show a 4.7 % mean km gap for the same reason.
   This is the root cause of most quality loss and it is invisible to the objective: the ant never
   evaluates the better plans.
2. **The pheromone contributes nothing measurable.** Running with `alpha=0` (pheromone ignored,
   pure heuristic sampling) produced the identical plan on 7 of 8 seeds and a slightly better mean
   fitness (8.400 vs 8.383). Two reasons: (a) the search space per ant is only the pickup order of a
   fixed balanced split, and (b) every ant deposits `Q·f/f_max` with fitness values that differ by
   a few percent, so deposits are near-uniform and the update is frequency reinforcement, not
   quality reinforcement. There are no pheromone bounds (MMAS) and no elitism.
3. **The "efficiency" objective rewards cramming, not km.** `_evaluate` keeps the paper's
   seat-usage term Σ k/cap and a 0.25-per-active-driver penalty. In this app the driver set is
   fixed and every driver drives to the destination anyway, so both terms only push passengers
   into one car. Hand example: two drivers with two neighbours each, spread plan 46 km scores
   6.38, cramming all four into one car 70 km scores 7.15. The round-robin bug in (1) accidentally
   hides this; fixing (1) without fixing (3) would make plans worse. `total_matched` is a constant
   whenever seats suffice.
4. **Detour-aware mode is slow.** Construction calls `_marginal_insertion_cost` for every
   candidate × every driver at every step and `_evaluate` recomputes eight penalty families.
   Measured: 0.65 s (6 passengers), 1.9 s (10), 4.6 s (15), 9.3 s (20). Efficiency mode is
   0.1–0.5 s on the same instances. The pipeline runs the solver once per driver set plus meetup
   variants, so the Detour-Aware set dominates wall time.
5. **Results are not reproducible.** The solver uses the global `random` module unseeded, so
   the same participants can yield a different plan on every click. Tests pin behaviour by seeding
   globally.
6. Minor: `_solution_sort_key` recomputes every route metric that `_evaluate` just computed
   (double work per ant); `feasible` in construction is never filtered; ride-together rules are
   only penalised after construction, so ants waste samples on plans the 1000-point penalty then
   discards; `_update_pheromones` skips deposits for fitness ≤ 0 (already noted in
   `docs/IMPROVEMENT_PLAN.md` §1.5).

### 1.2 What is sound and worth keeping

- The problem model: node indexing, distance matrix with OSRM override, capacity handling,
  soft time-window scheduling (`_route_time_analysis` picks the departure at a window breakpoint,
  which is exact for piecewise-linear violation), pickup-order and ride-together rules.
- `_refine_pickup_orders` (exhaustive ≤ 5 stops, 2-opt above) is a correct per-route TSP polish
  and is what actually produces good pickup orders today.
- `_repair_detour_aware_assignment` is a correct best-improvement relocate local search; it is
  just limited to one mode and to relocate moves.
- `_build_trip_result` and the time analysis are solver-independent and reusable.

### 1.3 Verdict

The ACO is valid as a randomized-greedy sampler with a good per-route polish, but it is not doing
ant-colony optimisation in any measurable sense, and its efficiency objective does not minimise km.
For groups of 4–20 people the search space is small enough that a deterministic
construct-and-improve local search finds the optimum in milliseconds; that should be the default,
with the ant kept as a selectable alternative once its objective and construction are fixed.

## 2. Algorithm survey

Problem class: multi-depot open capacitated VRP with soft time windows and one sink, i.e. the
"car pooling problem" of Baldacci, Maniezzo & Mingozzi (2004) / single-school bus routing (Park &
Kim 2010). Two facts drive every choice below: (a) with a fixed passenger→car assignment each car is
a ≤ 6-stop open TSP that brute force solves in microseconds, so **the hard part is the assignment**,
and (b) the assignment space for ≤ 20 people is small enough that a local search reaches the
optimum in well under a second of pure Python. Sources: §5.

Ease 1–5 (5 = easiest), LOC = pure-Python size, time = 20 people / 4 cars on a laptop in pure
Python (measured where marked *m*, otherwise estimated), quality = expected gap to optimum at this
size (100-node benchmark gaps where the literature has them).

| Algorithm | Source | Ease / LOC | Time | Quality | Fit for this app |
|---|---|---|---|---|---|
| Brute force / Held-Karp per car | Held & Karp 1962 | 5 / 20 | < 1 ms per car | optimal | Already used as `_refine_pickup_orders`; the evaluator inside everything else |
| Exhaustive assignment + exact routes | set partitioning, Balinski & Quandt 1964; Baldacci et al. 2004 | 4 / 40 | < 1 s up to ~8 passengers, exponential after | optimal | Good "prove it" option for small groups |
| Nearest neighbour / sweep | Gillett & Miller 1974 | 5 / 30 | < 10 ms | 10–25 % | Seed only |
| Clarke-Wright savings | Clarke & Wright 1964 | 5 / 60 | < 10 ms | 5–7 % | Needs adapting to driver-rooted routes; no time windows |
| Cheapest / regret insertion | Solomon 1987; Potvin & Rousseau 1993 | 4 / 80 | < 50 ms | 5–10 % (I1) | Natural seed; respects windows and pairs at insertion time |
| Relocate + swap descent | Croes 1958; Taillard et al. 1997 | 5 / 80 | 0.1–0.5 s | ≈ optimal at n ≤ 20, 3–5 % at 100 | **Core of the recommended default** |
| Iterated Local Search | Lourenço, Martin & Stützle 2003; FILO, Accorsi & Vigo 2021 | 5 / 80 on top of descent | ~1 s | ≈ optimal at this size | Deterministic with a fixed seed; robustness over plain descent |
| Large Neighbourhood Search / ALNS | Shaw 1998; Ropke & Pisinger 2006 | 4 / 200 | ~1 s | within ~1 % (PDPTW, VRPTW benchmarks) | Engine of jsprit; adaptive weights overkill here |
| Simulated Annealing | Kirkpatrick et al. 1983 | 5 / 60 | 2–3 s | within ~1 %, seed-dependent | Any objective works; nondeterministic |
| Tabu Search | Glover 1986; Cordeau & Laporte 2003 | 4 / 120 | 2–3 s | within ~1 % | Tenure/aspiration tuning |
| Variable Neighbourhood Search | Mladenović & Hansen 1997 | 4 / 100 | 2–3 s | within 1–2 % | Fewer parameters than SA |
| Guided Local Search | Voudouris & Tsang 1999 (OR-Tools default) | 4 / 100 | ~1 s | within ~1 % | ILS with a smarter perturbation |
| Ant colony (AS/ACS/MMAS/APCA) | Dorigo & Gambardella 1997; Stützle & Hoos 2000; Huang et al. 2019 | 3 / 200 | 0.1–0.5 s *m* (efficiency), up to 9 s *m* (detour-aware) | 1–3 % if hybridised, worse alone | Already here; many knobs; slow per unit of quality |
| Hybrid Genetic Search | Vidal et al. 2012; Vidal 2022; PyVRP 2024 | 2 / 600+ | population overhead | 0.1–0.3 % on Uchoa X (C++) | Advantages only appear at n ≫ 100 |
| SISR | Christiaens & Vanden Berghe 2020 | 3 / 250 | ~1 s | best-known on many Solomon/GH | String removal degenerates on ≤ 6-stop routes |
| LKH-3 | Helsgaun 2017 | n/a (C binary) | fast | best-known on most benchmarks | External process; not pure Python |
| Neural (attention model, POMO, neural LNS) | Kool et al. 2019; Kwon et al. 2020; Hottung & Tierney 2020 | 1 | GPU training | 1–3 % on 100-node random CVRP | PyTorch dependency and training for no gain at n ≤ 40 |

Rankings for this app (4–40 people, pure Python, interactive):

- **Ease:** per-car brute force > nearest neighbour / sweep > savings > relocate+swap descent >
  simulated annealing > ILS > regret insertion > VNS / tabu / GLS > LNS > ACO > ALNS > SISR >
  exhaustive assignment with solver > HGS > neural.
- **Speed to a good answer:** savings / sweep > insertion > descent > ILS / LNS > SA / VNS / tabu >
  ACO > exhaustive (n ≤ 8) > HGS.
- **Quality at this size:** exhaustive (optimal, n ≤ 8) > LNS ≈ ILS ≈ HGS (optimal or ~1 %) > SISR >
  SA / VNS / tabu / GLS (1–2 %, seed-dependent) > ACO (1–3 %) > descent from savings (3–5 %) >
  insertion (5–10 %) > savings > sweep > nearest neighbour.

No paper benchmarks 20-node multi-depot open instances; the quality column extrapolates from
100-node results and from the measured brute-force gaps in §1.

## 3. Selection

The app plans a night out for friends, not a delivery fleet: groups of 4–20, a result expected
within a second, a plan that does not change when you click again, and honest handling of time
windows and "ride together" rules. That rules out anything needing a compiled dependency (HGS,
PyVRP, OR-Tools, LKH-3) and anything needing training. Four solvers are exposed:

| Key | Name in UI | What it does | Default? |
|---|---|---|---|
| `local_search` | Local search | Cheapest-insertion seed, first-improvement relocate/swap descent on the full sort key, restarted 60× (12× in detour-aware mode) from 2–5 random relocate/swap kicks with a fixed RNG seed (iterated local search), then the existing per-route polish. Deterministic. Ride-together pairs move as one unit. | **Yes** |
| `annealing` | Simulated annealing | Random relocate/swap moves with Metropolis acceptance on the fitness (T₀ = 0.5 fitness units, geometric cooling to 0.005 over 30 × iterations steps, 4 500 by default), then the same descent and polish. | No |
| `ant_colony` | Ant colony (APCA) | The current solver with two fixes: drivers are picked at random per step instead of round-robin (unbalanced splits become reachable) and only the iteration-best and global-best ants deposit pheromone (elitist Ant System). | No |
| `exhaustive` | Exhaustive | Every capacity-feasible assignment with the km-optimal order per car, when `cars ^ passengers ≤ 20 000` (about 8 passengers); otherwise falls back to local search and says so. | No |

Why local search as default rather than LNS as the report suggests: at this instance size a
relocate/swap descent restarted from a handful of deterministic perturbations reaches the same
solutions, in less code, and is reproducible. LNS's destroy/repair pays off when routes are long
and neighbourhoods are too big to enumerate, which is not the case here.

**Objective fix shipped with this change:** in efficiency mode `_evaluate` drops the seat-usage
reward, the per-active-driver penalty and the paper's Gaussian per-route term (§1.1 finding 3;
the Gaussian exp(−d²/2σ²) with σ = 50 km is convex beyond 50 km, so for real 20–150 km routes it
too rewarded piling all distance onto one car — the descent moved a 166 km seed to a 177 km
cram plan with higher fitness until it was removed). Efficiency fitness is now
`matched − unassigned·10 − ride_together·1000 − total_km / reference − window_minutes·0.08 −
pickup_order·1.8`, i.e. km first with the existing soft-constraint penalties. Detour-aware mode
is unchanged. The displayed "Fitness" value drops by roughly 3–4 points compared with earlier
runs. The 0.08-per-minute window weight (≈ 13 km per minute on a 170 km trip) was inherited, not
calibrated; it is the one knob worth revisiting with real users.

Drop-down hints (Settings → Advanced optimizer): the `<select>` is followed by one card per solver
showing three bars (speed, plan quality, consistency) and one pro / one con line; the card for the
selected option is highlighted and swaps on change. The Results page shows a "Solver" badge in the
best plan's details so organizers comparing algorithms can see which produced the plan.

## 4. Implementation plan

1. `core/apca.py`: add `solver` parameter; move the ant loop into `_search_ant_colony`; add
   `_search_local`, `_search_annealing`, `_search_exhaustive`; shared post-processing stays in
   `solve()`; objective fix; ant fixes; `TripResult.solver_used`.
2. `core/config.py`: `DEFAULT_SOLVER = "local_search"`, `SOLVER_CHOICES`.
3. Settings plumbing: `app/services/settings.py` (`algorithm` key), `app/main.py` (form field,
   pass-through in the return-planning route), `app/services/planner.py` (`solver=`).
4. Template + CSS + JS: select, hint cards, results badge. i18n keys in it/fr/es.
5. Tests: brute-force parity for local search on seeded instances; annealing and exhaustive return
   complete plans; ant colony reaches the unbalanced split on the clustered instance; settings
   round-trip; hygiene suite stays green. Update the fitness floor in `test_apca_refinement.py`.

## 6. Measured results (worktree `route-solvers`, laptop, pure Python)

Random instances around Modena, cars seat 6, default settings, wall time / total km. Local search
and exhaustive are deterministic; annealing and ant colony use `random.seed(1)`.

| Instance | Mode | Local search | Annealing | Ant colony (fixed) | Exhaustive |
|---|---|---|---|---|---|
| 2 cars, 6 passengers | efficiency | 0.01 s / 156 km | 0.02 s / 156 km | 0.10 s / 156 km | 0.00 s / 156 km |
| 3 cars, 10 | efficiency | 0.02 s / 198 | 0.04 s / 198 | 0.21 s / 212 | 0.02 s / 198 |
| 3 cars, 15 | efficiency | 0.24 s / 244 | 0.01 s / 262 | 0.34 s / 234 | falls back |
| 4 cars, 20 | efficiency | 0.75 s / 293 | 0.03 s / 317 | 0.55 s / 310 | falls back |
| 3 cars, 10 | detour-aware | 0.21 s / 247 | 0.12 s / 257 | 2.1 s / 252 | 0.20 s / 247 |
| 4 cars, 20 | detour-aware | 3.1 s / 338 | 0.35 s / 338 | 9.2 s / 321 | falls back |
| Clustered 5+1 (optimum 82.1 km) | efficiency | 82.1 | 82.1 | 82.1 | 82.1 |

- Local search equals the brute-force km optimum on all eight seeded 2×6 instances (the old ant
  averaged +4.7 %) and on the clustered instance (old ant +68 %).
- At 15–20 passengers no optimum is known; the fixed ant colony found plans 2–4 % shorter than
  local search on two of four instances, and local search found the shortest plan on the others.
  Larger kicks or a 2-opt polish inside moves did not close that gap consistently and cost time,
  so the default stays with 60 restarts.
- Detour-aware evaluation is ~20× dearer per solution (eight penalty families with marginal
  insertion costs) and now dominates the pipeline's wall time; speeding up
  `_evaluate` in that mode is the next optimisation, not the search.

## 5. References

- Accorsi, L., Vigo, D. (2021). A fast and scalable heuristic for the solution of large-scale capacitated vehicle routing problems. *Transportation Science* 55(4). https://pubsonline.informs.org/doi/10.1287/trsc.2021.1059
- Agatz, N., Erera, A., Savelsbergh, M., Wang, X. (2012). Optimization for dynamic ride-sharing: A review. *EJOR* 223(2). https://doi.org/10.1016/j.ejor.2012.05.028
- Baldacci, R., Maniezzo, V., Mingozzi, A. (2004). An exact method for the car pooling problem based on Lagrangean column generation. *Operations Research* 52(3). https://pubsonline.informs.org/doi/10.1287/opre.1030.0106
- Balinski, M.L., Quandt, R.E. (1964). On an integer program for a delivery problem. *Operations Research* 12(2). https://pubsonline.informs.org/doi/10.1287/opre.12.2.300
- Bräysy, O., Gendreau, M. (2005). VRPTW Part I: Route construction and local search algorithms. *Transportation Science* 39(1). https://dl.acm.org/doi/10.1287/trsc.1030.0056
- Christiaens, J., Vanden Berghe, G. (2020). Slack Induction by String Removals for Vehicle Routing Problems. *Transportation Science* 54(2). https://pubsonline.informs.org/doi/10.1287/trsc.2019.0914
- Clarke, G., Wright, J.W. (1964). Scheduling of vehicles from a central depot to a number of delivery points. *Operations Research* 12(4). https://pubsonline.informs.org/doi/10.1287/opre.12.4.568
- Cordeau, J.-F., Laporte, G. (2003). A tabu search heuristic for the static multi-vehicle dial-a-ride problem. *Transportation Research B* 37(6). https://www.sciencedirect.com/science/article/abs/pii/S0191261502000450
- Cordeau, J.-F., Laporte, G. (2007). The dial-a-ride problem: models and algorithms. *Annals of OR* 153. https://link.springer.com/article/10.1007/s10479-007-0170-8
- Dorigo, M., Gambardella, L.M. (1997). Ant colony system. *IEEE Trans. Evolutionary Computation* 1(1). https://doi.org/10.1109/4235.585892
- Furuhata, M. et al. (2013). Ridesharing: The state-of-the-art and future directions. *Transportation Research B* 57. https://econpapers.repec.org/RePEc:eee:transb:v:57:y:2013:i:c:p:28-46
- Gillett, B.E., Miller, L.R. (1974). A heuristic algorithm for the vehicle-dispatch problem. *Operations Research* 22(2). https://pubsonline.informs.org/doi/10.1287/opre.22.2.340
- Glover, F. (1986). Future paths for integer programming and links to artificial intelligence. *Computers & OR* 13(5). https://doi.org/10.1016/0305-0548(86)90048-1
- Held, M., Karp, R.M. (1962). A dynamic programming approach to sequencing problems. *J. SIAM* 10(1). https://doi.org/10.1137/0110015
- Helsgaun, K. (2017). An extension of the Lin-Kernighan-Helsgaun TSP solver for constrained TSP and VRP. http://webhotel4.ruc.dk/~keld/research/LKH-3/LKH-3_REPORT.pdf
- Hottung, A., Tierney, K. (2020). Neural large neighborhood search for the CVRP. *ECAI 2020*. https://github.com/ahottung/NLNS
- Huang, S.-C., Jiau, M.-K., Liu, Y.-P. (2019). An ant path-oriented carpooling allocation approach to optimize the carpool service problem with time windows. *IEEE Systems Journal* 13(1), 994–1005. https://ieeexplore.ieee.org/document/8318642/
- Kirkpatrick, S., Gelatt, C.D., Vecchi, M.P. (1983). Optimization by simulated annealing. *Science* 220. https://www.science.org/doi/10.1126/science.220.4598.671
- Kool, W., van Hoof, H., Welling, M. (2019). Attention, learn to solve routing problems! *ICLR*. https://arxiv.org/abs/1803.08475
- Kwon, Y.-D. et al. (2020). POMO: Policy optimization with multiple optima. *NeurIPS*. https://arxiv.org/abs/2010.16011
- Lourenço, H.R., Martin, O.C., Stützle, T. (2003). Iterated Local Search. *Handbook of Metaheuristics*. https://link.springer.com/chapter/10.1007/0-306-48056-5_11
- Mladenović, N., Hansen, P. (1997). Variable neighborhood search. *Computers & OR* 24(11). https://doi.org/10.1016/S0305-0548(97)00031-2
- Park, J., Kim, B.-I. (2010). The school bus routing problem: A review. *EJOR* 202(2). https://www.sciencedirect.com/science/article/abs/pii/S037722170900349X
- Potvin, J.-Y., Rousseau, J.-M. (1993). A parallel route building algorithm for the VRP and scheduling problem with time windows. *EJOR* 66(3). https://www.sciencedirect.com/science/article/abs/pii/0377221793902218
- Ropke, S., Pisinger, D. (2006). An adaptive large neighborhood search heuristic for the PDPTW. *Transportation Science* 40(4). https://doi.org/10.1287/trsc.1050.0135
- Shaw, P. (1998). Using constraint programming and local search methods to solve vehicle routing problems. *CP 1998*. https://doi.org/10.1007/3-540-49481-2_30
- Solomon, M.M. (1987). Algorithms for the vehicle routing and scheduling problems with time window constraints. *Operations Research* 35(2). https://doi.org/10.1287/opre.35.2.254
- Stützle, T., Hoos, H.H. (2000). MAX-MIN Ant System. *Future Generation Computer Systems* 16(8). https://www.sciencedirect.com/science/article/abs/pii/S0167739X00000431
- Taillard, É. et al. (1997). A tabu search heuristic for the VRP with soft time windows. *Transportation Science* 31(2). https://pubsonline.informs.org/doi/10.1287/trsc.31.2.170
- Uchoa, E. et al. (2017). New benchmark instances for the CVRP. *EJOR* 257(3). https://doi.org/10.1016/j.ejor.2016.08.012
- Vidal, T. et al. (2012). A hybrid genetic algorithm for multidepot and periodic VRPs. *Operations Research* 60(3). https://doi.org/10.1287/opre.1120.1048
- Vidal, T. (2022). Hybrid genetic search for the CVRP: open-source implementation and SWAP* neighborhood. *Computers & OR* 140. https://arxiv.org/abs/2012.10384 ; https://github.com/vidalt/HGS-CVRP
- Voudouris, C., Tsang, E. (1999). Guided local search and its application to the TSP. *EJOR* 113(2).
- Wouda, N.A., Lan, L., Kool, W. (2024). PyVRP: a high-performance VRP solver package. *INFORMS J. Computing* 36(4). https://doi.org/10.1287/ijoc.2023.0055 ; https://github.com/PyVRP/PyVRP
- Yan, S., Chen, C.-Y. (2011). An optimization model and a solution algorithm for the many-to-many car pooling problem. *Annals of OR* 191. https://link.springer.com/article/10.1007/s10479-011-0948-6
- Google OR-Tools routing options: https://developers.google.com/optimization/routing/routing_options ; VROOM: https://github.com/VROOM-Project/vroom ; jsprit: https://github.com/graphhopper/jsprit
- Solomon / Gehring-Homberger benchmark tables: https://www.sintef.no/projectweb/top/vrptw/homberger-benchmark/

Uncertainty: the Croes 1958 2-opt attribution and the "savings ≈ 5–7 %" figure are from memory
and not re-verified; pure-Python timings not marked *m* are estimates.
