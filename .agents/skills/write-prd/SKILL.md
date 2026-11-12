---
name: write-prd
description: Turn the shared understanding from a grill session into a PRD. For a feature, create it as a GitHub issue labelled prd. For the whole project, write docs/PRODUCT.md. Use after /grill-me.
---

# Write a PRD

Use only what we discussed. Do not add ideas we did not agree on.

## If I say "project" (first time only)

Fill in `docs/PRODUCT.md` (Problem, Users, What the first version does, Not now, Non-negotiables, Tech stack).
Copy the non-negotiables into `AGENTS.md`. Then stop and show me the file.

## Otherwise: a feature PRD as a GitHub issue

Write the body with these sections:

1. **Problem** – what is wrong today, for whom.
2. **Solution** – what we will build, in a few sentences.
3. **User stories** – "As a <user>, I can <action>, so that <benefit>." Number them.
4. **Modules to change** – each module that changes. For any NEW module, give its public interface. Prefer one deep module over many small ones.
5. **Testing decisions** – what we test, and at which module boundary.
6. **Non-negotiables touched** – rules from AGENTS.md this feature affects.
7. **Out of scope** – what we decided not to do, and why.

Show me the body first. When I approve, create it:

```
gh issue create --title "PRD: <feature name>" --label prd --body-file <tmp file>
```

Tell me the issue number and stop.
