---
name: next
description: The project guide. Checks where the project is (setup, vision, protection, Superset, tickets, pull requests) and does the next step or tells the user exactly what to do. Use when the user types /next, asks "what now?", or starts a new session in this project.
---

# Next step

You are the project guide. Find the FIRST phase below that is not done, then do it.
Do what you can yourself. For steps only the human can do, give exact instructions and wait.
Talk in `ste` style. End every turn with one line: **Next:** <the one thing to do>.

Setup flags are GitHub repo variables, so every worktree sees them:
read with `gh variable list`, write with `gh variable set <NAME> --body <value>`.

---

## Phase 0 — Repo ready?

Check: `gh repo view` works and `gh label list` shows `prd`, `afk`, `agent:claude`.
If labels are missing: run `./scripts/setup-labels.sh`.

## Phase 1 — Product vision

Done when `docs/PRODUCT.md` no longer contains `[Who has which problem today.]`.

If not done:
1. Read `.kit/idea.md` if it exists. Say: "Let's define the product. I'll interview you, one question at a time. Answer, or say 'your call'."
2. Follow the `grill-me` skill. Goal: problem, users, the one main flow, the first version (MVP), what is NOT in it, non-negotiables, tech stack. Start from the idea, do not ask what it already answers.
3. When aligned, follow `write-prd` in "project" mode: fill `docs/PRODUCT.md` and the non-negotiables + project sentence in `AGENTS.md`. Also set the real test, type-check and lint commands for the chosen stack.
4. Show the files. On approval:
   - If `gh variable list` has no `KIT_PROTECTION`: commit and `git push` to main.
   - Otherwise: create branch `docs/product-vision`, commit, push, `gh pr create`. Tell the user to merge it.

## Phase 2 — Protect main

Done when `gh variable list` has `KIT_PROTECTION`.

Run `./scripts/protect-main.sh`.
- Exit 0: `gh variable set KIT_PROTECTION --body protected`. Say: "Main is protected. From now on every change goes through a pull request with green CI."
- Exit 2: explain that GitHub refused (likely a private repo on the free plan). Offer: (a) make the repo public, (b) upgrade the plan, (c) continue with a rule we follow by hand: merge only when CI is green. On (c): `gh variable set KIT_PROTECTION --body manual`.

## Phase 3 — Superset setup (human)

Done when `gh variable list` has `KIT_SUPERSET` = `done`.

Give these steps, filled in with the real folder path and repo name:

1. **Add the project:** in Superset, add the local folder `<absolute path of this repo>`.
2. **Connect GitHub:** Plugins → GitHub → connect. Approve the scopes `repo`, `read:org`, `workflow`.
3. **Task Prompt Template:** Settings → Agents. For Claude Code AND Cursor Agent, paste this (replace the angle brackets with Superset's placeholders for issue number, title and description):
   ```
   Read AGENTS.md. Use the implement skill on GitHub issue #<number>: <title>.

   <description>
   ```
4. **Auto-run command:** leave it OFF for now.
5. Optional: Settings → turn on notification sounds.

Ask: "Tell me 'done' when the four steps are finished." On "done": `gh variable set KIT_SUPERSET --body done`.

## Phase 4 — Plan the MVP

Done when `gh issue list --label prd --state all` is not empty.

1. Say: "Now we plan the first version from docs/PRODUCT.md. Short interview first."
2. Follow `grill-me` for the MVP only. Questions only about what PRODUCT.md leaves open.
3. Follow `write-prd` (feature mode): create the PRD issue.
4. Follow `prd-to-issues`. The FIRST ticket must be the skeleton: project set up, one tiny feature visible end to end, tests + type check + lint running, so CI goes green on real code. All other tickets are blocked by it.
5. Then go to Phase 5.

## Phase 5 — The daily loop

Collect the state:
- Open PRs: `gh pr list --json number,title,headRefName,statusCheckRollup,reviewDecision,comments`
- Open tickets: `gh issue list --state open --json number,title,labels,body`
- Recently closed tickets: `gh issue list --state closed --limit 30 --json number`

Then, in this order:

1. **Unblock.** For each open ticket labelled `blocked`: read "Blocked by" in its body. If all those issues are closed, run `gh issue edit <n> --remove-label blocked` and say so.
2. **PRs first** (finish work before starting work). For each open PR, say which ONE step it needs:
   - CI failing → "In its workspace, ask the builder to fix CI." (Offer to diagnose: `gh pr checks`, `gh run view --log-failed`.)
   - No review comment yet → "In its workspace, open a terminal with the OTHER agent and run review." (Built by `agent:claude` → Cursor: "Use the review skill (.agents/skills/review/SKILL.md)"; `agent:cursor` → Claude `/review`.)
   - Review posted, fixes not pushed → "Ask the builder to fix the BLOCKER and SHOULD FIX items."
   - CI green and reviewed → "Run the app in that workspace and click through it. Agentation notes go to the builder. When happy: Merge → Squash and merge, then delete the workspace."
3. **Start new work.** Ready tickets = open, `afk`, not `blocked`, no open PR for them. Suggest at most 3 minus the open PRs. Avoid two tickets that touch the same module. For each: "Superset → Tasks → GitHub → select #<n> → Run in workspace → <agent from its label>."
4. **Human tickets.** List open `human` tickets: these need the user.
5. **Feature done?** If every ticket of a PRD is closed: close the PRD issue with a short summary comment. Count closed PRDs: after every 3rd, suggest a health check (drift vs PRODUCT.md, deeper modules, security review, try to break the main flows). Then ask: "What feature next? Tell me the idea and we start the interview."

Show the result as a short list grouped: **Merge**, **Review**, **Fix**, **Start**, **Needs you**. Skip empty groups.
