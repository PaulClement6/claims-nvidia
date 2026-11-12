---
name: prd-to-issues
description: Break a PRD issue into small GitHub issues (tickets) using vertical slices, with labels for agent and autonomy and explicit blockers. Use after /write-prd.
---

# PRD to tickets

Read the PRD with `gh issue view <number>`. If I give no number, use the newest open issue labelled `prd`.

## Rules

- Each ticket is a **vertical slice**: it touches every layer it needs (data, logic, UI) and gives something you can see or test end to end.
- Never a horizontal ticket like "build the database layer" alone.
- The first ticket is the thinnest possible end-to-end slice.
- Each ticket fits in one short agent session. If not, split it.
- Keep blockers to a minimum so tickets can run in parallel. Two tickets that would edit the same files should block each other.
- Labels on every ticket:
  - `afk` (an agent can do it alone) or `human` (needs my decision or eyes)
  - `agent:claude` or `agent:cursor` – suggest Claude for UI and product logic, Cursor for backend logic, data and refactors. I can change it.
  - `blocked` if it has open blockers.

## Process

1. Show me a table: #, title, labels, blocked by. One line each.
2. Wait for my approval or changes.
3. Create the tickets in order, so blockers exist first. Use real issue numbers in "Blocked by".

```
gh issue create --title "<title>" --label afk --label agent:claude --body-file <tmp file>
```

4. Comment on the PRD issue with the list of tickets.
5. Show me the final list with numbers.

## Ticket body

```
PRD: #<prd number>
User stories: 1, 3
Blocked by: none | #12, #13

## What to build
<2–5 sentences>

## Use
Skills: implement, then review by the other agent
Tools: <MCP servers needed, or "none">

## Done when
- [ ] <observable result>
- [ ] Tests written first (TDD) and passing
- [ ] Type check passing
```
