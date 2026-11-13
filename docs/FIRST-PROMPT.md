# First prompt

Historical. This is the prompt that started the project on 2026-11-12, when it had documents only. Do not run it again: the repository, the issues and the code exist (see `AGENTS.md`, section Status).

Open a terminal in this folder, start Claude Code, and paste the prompt below.

```
Read AGENTS.md, then every file in docs/. This project has documents only: no code, no git repository, no GitHub issues.

Do these steps in order. Stop after each step and wait for my answer.

1. Run /grill-me on the open decisions O1 to O13 of docs/DECISIONS.md, and on anything in docs/PRODUCT.md, docs/WORKFLOWS.md and docs/FRAUD.md that is not clear or that you think is wrong. Ask one question at a time. Before you ask about the NVIDIA stack, check the current docs (docs/READING-LIST.md) and read ../nemoforge-plateforme/docs/DECISIONS.md and LESSONS.md for what already works for us.

2. Update docs/DECISIONS.md, docs/PRODUCT.md and docs/ARCHITECTURE.md with my answers. Show me the diff.

3. Run git init, make the first commit, and create the private repository PaulClement6/claims-nvidia with gh. Set up the labels of AGENTS.md.

4. Run /write-prd for milestones M0 to M2 of docs/BACKLOG.md (the full flow in local mode, for the three mock claim files). Then run /prd-to-issues. Create the other milestones as issues too, with the label intern where the backlog says so.

5. Start the first issue with /implement.
```
