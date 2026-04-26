# Grading and reward-design research for Jira/Outlook triage

## Current weakness

The current environment mostly rewards tool use with small fixed bonuses and gives a single large terminal reward for the final category. That teaches task completion weakly, but it does not strongly teach *why* the agent should gather the right evidence before answering.

## What good grading should teach

A stronger system should reward four things:

1. retrieving relevant evidence,
2. covering the right evidence sources,
3. making the correct final classification,
4. doing all of that efficiently.

## Research-backed ideas

### 1. Use potential-based shaping for progress rewards

Potential-based reward shaping preserves the optimal policy while adding dense guidance, if the shaping reward is defined as a difference in a potential function over states. That is a strong fit here because the environment can define progress signals such as “has found duplicate evidence” or “has found a request-for-info mail” without changing the final task objective. citeturn0search0turn0search1

**Practical design:**
- Define a potential over evidence coverage, like number of required clues found.
- Give shaped reward only as `gamma * Phi(next_state) - Phi(state)`.
- Keep the terminal classification reward as the dominant signal.

### 2. Grade evidence, not just the final label

Information-seeking and retrieval agents learn better when intermediate evidence quality matters, because otherwise they can guess the label without using tools well. Retrieval evaluation work commonly looks at ranking quality, recall, and whether the right evidence appears near the top. citeturn0search0

**Practical design:**
- For `duplicate`, require discovery of at least one prior closed Jira with strong lexical overlap.
- For `needs_more_info`, require finding at least one Outlook message asking for repro steps, logs, or stack traces.
- For `unclosed/new`, require absence of duplicate evidence and absence of actionable follow-up evidence.
- Score final answers higher when the agent has gathered the expected evidence first.

### 3. Reward source coverage and ordered reasoning

The task naturally has two sources: Jira and Outlook. A better agent should learn when to inspect both, not only one. In sequential decision tasks, reward design that values useful exploration and state coverage can improve policy quality when the final outcome depends on hidden evidence. This is an inference from RL reward-shaping literature and information-seeking settings, rather than one single exact paper for this environment. citeturn0search1turn0search2

**Practical design:**
- Small bonus when the agent retrieves from the source that is actually informative for the current task.
- No repeated bonus for redundant calls that return the same record again.
- Mild penalty for excessive repeated lookups with no new information.

### 4. Add classification-specific rubrics

Each resolution class should have its own rubric.

**Duplicate rubric**
- Prior Jira retrieved.
- Prior Jira resolution is `closed`.
- Text overlap or same subsystem/build symptoms found.
- Final answer names the duplicate ticket.

**Needs-info rubric**
- Outlook mail retrieved that requests reproduction details, logs, or traces.
- Final answer selects `needs_more_info`.
- Bonus if the agent cites the missing items explicitly.

**Unclosed/new rubric**
- No strong duplicate evidence found.
- No clear “needs more info” evidence found.
- Final answer stays conservative.

### 5. Use retrieval-aware scoring for search actions

SQLite FTS5 uses BM25-style ranking. Search actions should be rewarded by whether they surface the gold evidence near the top, not just whether any result exists. SQLite documents BM25 ranking behavior for FTS5, and IR evaluation generally values rank-sensitive metrics like reciprocal rank and top-k recall. citeturn0search0

**Practical design:**
- Reward search more when the gold evidence is ranked at position 1 than at position 5.
- Use a shaped signal like `1 / rank` for the first gold hit.
- Give zero or tiny reward for noisy searches that miss the gold evidence.

### 6. Penalize premature submission

A common failure mode is early guessing. The environment should discourage submitting before enough evidence is collected.

**Practical design:**
- If the agent submits without minimum evidence coverage, cap reward even if the guess is accidentally correct.
- Example: correct answer without supporting evidence gets `0.3`, while correct answer with evidence gets `1.0`.

### 7. Track novelty of discovered evidence

The state already tracks discovered Jira and mail ids. That is useful because novelty-based rewards are often better than per-action rewards in tool-use settings.

**Practical design:**
- Reward the first retrieval of a relevant ticket or mail.
- Give zero reward for repeated retrievals of the same id unless different fields reveal new required evidence.

## Suggested concrete grading redesign

### Terminal reward
- Correct class with sufficient evidence: `+1.0`
- Correct class with weak evidence: `+0.3`
- Wrong class after good investigation: `-0.4`
- Wrong class with weak investigation: `-1.0`

### Search reward
- Gold evidence at rank 1: `+0.15`
- Gold evidence in top 3: `+0.10`
- Gold evidence in top 5: `+0.05`
- No gold evidence: `0` or slight negative

### Retrieval reward
- First relevant record retrieved: `+0.05`
- First irrelevant record: `0`
- Repeated retrieval of same record: `0`

### Efficiency penalty
- Small step cost, like `-0.01` per action.
- Extra penalty for repeated no-gain searches.

## Best next implementation steps

1. Add gold-evidence annotations to each task file.
2. Add a helper that checks evidence coverage from state.
3. Replace fixed search rewards with rank-sensitive rewards.
4. Make final reward depend on both correctness and evidence sufficiency.
5. Log reward components separately for debugging and offline analysis.

## Bottom line

The biggest improvement is to move from “correct final label only” to “correct final label supported by good evidence retrieval.” That should teach the agent to use Jira and Outlook as reasoning tools instead of guessing early. citeturn0search1turn0search2
