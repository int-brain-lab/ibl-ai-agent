# Eval grid results

15 models x 3 questions, 45 runs, 27 passed (60%).

| model                  | bwm-neuron-count-by-region     | bwm-visual-cortex-by-hemisphere   | skill-acg-cortical-depth   |   pass % |   input tokens (incl. cache) |   output tokens | cost         |
|:-----------------------|:-------------------------------|:----------------------------------|:---------------------------|---------:|-----------------------------:|----------------:|:-------------|
| claude-opus-5          | pass                           | pass                              | pass                       |      100 |                       620156 |            9970 | $1.50        |
| claude-sonnet-5        | pass                           | pass                              | pass                       |      100 |                       675250 |            9210 | $0.66        |
| gemma-4-31B-it         | pass                           | pass                              | pass                       |      100 |                       121947 |            3566 | not reported |
| gpt-5-mini             | pass                           | pass                              | pass                       |      100 |                       105220 |            8814 | not reported |
| Qwen3.8-27B            | pass                           | pass                              | fail (length)              |       67 |                       331419 |           14819 | not reported |
| claude-haiku-4.5       | fail (answered)                | pass                              | pass                       |       67 |                       275809 |            7371 | $0.21        |
| ministral-8b-latest    | fail (answered)                | pass                              | pass                       |       67 |                       186210 |            7267 | not reported |
| mistral-small-latest   | fail (answered)                | pass                              | pass                       |       67 |                       169085 |            7480 | not reported |
| deepseek-v4.1-flash    | fail (max_turns)               | fail (max_turns)                  | pass                       |       33 |                       379315 |           10677 | not reported |
| gemini-2.5-flash       | fail (malformed_function_call) | fail (malformed_function_call)    | pass                       |       33 |                        29008 |              21 | not reported |
| gpt-5-nano             | fail (answered)                | fail (answered)                   | pass                       |       33 |                       132056 |            5364 | not reported |
| magistral-small-latest | fail (answered)                | fail (answered)                   | pass                       |       33 |                       199132 |            9664 | not reported |
| ministral-14b-latest   | fail (max_turns)               | fail (answered)                   | pass                       |       33 |                       205123 |            3928 | not reported |
| ministral-3b-latest    | fail (answered)                | fail (max_turns)                  | pass                       |       33 |                       346126 |           13085 | not reported |
| mistral-medium-latest  | fail (max_turns)               | fail (answered)                   | pass                       |       33 |                       395796 |           14488 | not reported |
