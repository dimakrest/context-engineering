---
name: mission-status
description: Render a self-contained HTML status page for a mission from its state.md and journal.jsonl - assertion coverage, feature progress, spend vs cap, what is running now, open issues. Use when the user asks how a mission is going, says "/missions:mission-status", or wants to check in on a long run.
user_invocable: true
---

# /missions:mission-status — async oversight in one page

Turn `.missions/<slug>/` into a page a human can read in thirty seconds without following the run.
No new UI surface: it's a self-contained HTML file in the same house style as the repo's other
findings pages, opened in a browser.

## Read

`state.md`, `contract.md`, `features.md`, `journal.jsonl`, `followups.md`, and `handoffs/*.md`
(summaries only — do not paste whole handoffs into the page).

If more than one mission directory exists and the user didn't name one, list them and ask.

## Render — in this order

1. **Headline band.** Phase · current milestone · what is running right now (or "idle — awaiting
   decision", with the decision named) · spend vs cap.
2. **Assertion coverage.** The primary metric. A stacked bar or table of
   `proven / claimed / unproven`, split by proof class (`structural`, `conversational`, `interface`)
   — the split matters because conversational assertions are the slow, expensive ones and their
   backlog predicts remaining wall-clock better than feature count does.
3. **Features.** One row each: id, title, milestone, status, commit sha, assertions covered.
4. **Open issues.** Anything blocking progress, loudest element on the page after the headline. If
   the mission is halted, the reason and the exact decision needed go here.
5. **Milestone timeline.** From the journal: dispatches, validator verdicts, retries. Show validation
   failures rather than hiding them — first-pass failure is expected, and a milestone that passed
   everything first try is worth a second look at whether the assertions bite.
6. **Cost.** Dollars from `session_cost` events (last value per session, summed) and the current
   session via `bash "${CLAUDE_PLUGIN_ROOT}/scripts/mission-spend.sh" <transcript> <journal>`;
   agent wall-clock from `agent_return.duration_s`; dispatches used vs the dispatch cap; calls placed
   if the behavior validator ran. Tokens, if any were journaled, are estimates — label them so.
   Also print the five acceptance metrics and the seats line (dispatches and agent-hours per
   model — what actually ran, not what `mission.md` planned) from
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/journal-metrics.sh" .missions/<slug>`.
7. **Follow-ups.** Contents of `followups.md`.

## Rules

- Diagrams and tables over prose (repo house style for findings pages).
- Self-contained: inline CSS, no external assets, works from `file://`, respects light and dark via
  `prefers-color-scheme`.
- Write to `docs/plans/<slug>-status.html`. Never commit it.
- **State what is unknown as unknown.** If the journal has no token estimates, the cost section says
  "not recorded" — it does not interpolate. A status page that quietly invents numbers is worse than
  no status page, because it will be believed.
- End the chat reply with a two-line summary and the file path.
