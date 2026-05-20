# Randomization and permutation tests
- When analyzing randomly generated experimental variables, use randomization tests: N times, randomly generate a pseudosession for each session, with the variable regenerated according to the probability rule that generated it. E.g. when randomizing visual contrast, use the p_left block probabilities.
- For variables not randomly generated, prefer stratified permutation tests. E.g. for differences between brain regions, randomize each unit's assigned region *within a probe or session*, so not to mistake differences between probes for between areas. 
- normalize p values by (N+1). Take N=10^p -1, eg 9,999, to get round fractions.
- consider computation time before running tests.

# Caveats
- Simultaneously-recorded neurons are not statistically independent
- Behavioral choices and neural activity are not independent across trials

# Keep contrasts few 
- To avoid lowering statistical power, don't compare between many conditions/areas. For example consider Cosmos rather than Beryl brain regions as primary test, saving Beryl for posthoc.