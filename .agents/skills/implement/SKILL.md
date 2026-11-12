---
name: implement
description: Implement one GitHub issue (ticket) with TDD on the current branch, then push and open a pull request. Use in a fresh session or a new Superset workspace, one ticket per session.
---

# Implement one ticket

If I give an issue number, use it. Otherwise ask me.

1. Read the ticket: `gh issue view <number>`. Read its PRD issue and `AGENTS.md`.
2. If a blocker in "Blocked by" is still open, stop and tell me.
3. Make sure you are NOT on `main`. If you are, create a branch: `git switch -c <number>-<short-name>`.
4. Explore only the code this ticket needs.
5. TDD: write one failing test, run it and see it fail, make it pass, refactor. Repeat for each "Done when" item.
6. Run tests, type check and lint. Fix every failure.
7. Commit with a message that starts with `#<number>`.
8. Push and open a pull request:

```
git push -u origin HEAD
gh pr create --title "#<number> <ticket title>" --body "Closes #<number>

<what you built, 3–5 lines>
<what I must check by hand>"
```

9. Report in 5 lines or fewer: PR link, what you built, what you tested, what I must check by hand.

## Fixing review findings

If I ask you to fix review findings on the same ticket: fix only those, run the checks, commit, and `git push`. The PR updates by itself.

## Rules

- Do only this ticket. If you see other problems, list them at the end. Do not fix them.
- If the ticket is unclear, stop and ask. Do not guess.
- Never push to `main`. Never merge.
