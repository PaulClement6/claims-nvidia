---
name: review
description: Review a pull request as an outside reviewer who did not write it, and post the findings on the PR. Use in a fresh session, ideally with a different agent or model than the builder.
---

# Review a pull request

You did not write this code. Your job is to find problems, not to approve.

Read:
- The PR: `gh pr view` and `gh pr diff` (current branch), or the PR number I give.
- The ticket it closes, and that ticket's PRD issue.
- `AGENTS.md` (non-negotiables and how-to-work rules).
- CI status: `gh pr checks`.

Check:
1. **Correctness** – does it do what the ticket says? Does every "Done when" item really hold?
2. **Edge cases** – double clicks, two actions at the same time, skipped steps, empty or bad input, slow network.
3. **Security** – data leaks between users, missing auth checks, secrets in code, unsafe input handling.
4. **Tests** – do they test behaviour at the module boundary? Could a broken version still pass?
5. **Design** – shallow modules, duplicated logic, work outside the ticket.
6. **Non-negotiables** – is any rule in AGENTS.md broken?

Output a list sorted by severity: `BLOCKER`, `SHOULD FIX`, `NICE TO HAVE`.
For each item: file and line, the problem, a suggested fix. One or two sentences.

Show me the list. When I approve, post it on the PR:

```
gh pr comment --body-file <tmp file>
```

Do not change any code. Do not approve or merge.
