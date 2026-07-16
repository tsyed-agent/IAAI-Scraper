# Project tracker — how it works

**Open:** [`project-tracker.html`](./project-tracker.html) (open in a browser).

This is the living status board for active plans
([`09-enterprise-hardening-plan.md`](./09-enterprise-hardening-plan.md),
[`10-implementation-plan.md`](./10-implementation-plan.md),
[`handoffs/`](./handoffs/)). Deprecated / superseded plans are not listed.

**Layout (single sequence):**

| Phase | Focus |
|---|---|
| **0** | Review fixes & hot-path hardening (current) |
| **1** | Media API (shipped) |
| **2** | Production P0 gates |
| **3** | Website BFF / UI |
| **4** | Multi-source / PostgreSQL |
| **5** | Operations & scale |

Under each phase: **Task 1, Task 2, …** (plan IDs like `0.6a` shown as secondary refs).
No duplicate “Doc 09 / Doc 10 / Handoff” sections — one place per task.

## Agent rule (mandatory, keep short)

After finishing (or blocking) a task:

1. Update **that task only** in `docs/project-tracker.html`.
2. Set status badge: `completed` | `in-progress` | `partial` | `blocking` | `testing` | `test-failed` | `issue` | `deferred` | `open`.
3. Toggle the checkbox when done.
4. Append **one** short comment (≤2 lines) with your signature tag:
   - `<span class="sig cursor">Cursor</span>`
   - `<span class="sig codex">Codex</span>`
   - `<span class="sig claude">Claude Code</span>`
5. If you opened/merged a PR or landed commits, add links under **PR / commits** on that task.
6. Bump `data-updated` on `<html>` and the “Last board update” line in the hero.
7. Do **not** rewrite phases, paste long logs, or duplicate the markdown plans.

Plans remain source of truth for acceptance criteria; the HTML board is for
status, sequencing, and handoff visibility.
