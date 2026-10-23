# Agent evaluation: qwen3:4b

Config: max_tool_calls=6, max_revisions=1, grade=True, prompts 6a94ecfd6cef, golden 63db9471183a

- **Accuracy: 88%** (95% CI 69%-100%) over 16 questions, 16 runs, 0 errors
- Consistency (right in every run): -
- Retrieved a gold chapter: 71%; cited a gold chapter: 71%
- Grader said supported: 62%; runs citing something never retrieved: 19%
- Tool calls per run: 2.0625; revised: 88%; budget exhausted: 0; seconds per run: 102.9688

| Category | Accuracy |
|---|---|
| single_fact | 89% |
| who_said | 100% |
| multi_hop | 67% |
| unanswerable | 100% |

## Questions not always answered correctly

| Question | Category | Correct | Retrieved gold | Example wrong answer |
|---|---|---|---|---|
| How does Marmeladov die? | single_fact | 0/1 | y | Marmeladov dies in Part II, Chapter VII after a short struggle, recognizing Katerina Ivanovna and his daughter Sonia [Part II, Chapter VII, passage 29]. |
| Who is the father of the young woman who read the story of Lazarus to Raskolnikov? | multi_hop | 0/1 | y | The book passages you found do not state the father's name of the young woman who read the story of Lazarus to Raskolnikov. The search results identify Sonia as |
