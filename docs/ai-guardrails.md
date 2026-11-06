# AI-Assisted Development Guardrails

*Example draft written for the Practice_Repo project, to adapt for a team. Review it whenever the tools change.*

## Purpose
Let developers use AI tools (Copilot, IDE agents, coding agents) to move faster without weakening security, quality, or supportability. The platform team owns this document and keeps the safe path the easy one.

## Principles
- **AI proposes, people decide.** The author of a change is responsible for it, however it was written.
- **Same standards as any code.** AI-written changes go through the same review, tests, and scans.
- **Least privilege.** Tools and agents get the smallest access that does the job, for the shortest time.
- **Make the safe path the easy one.** Sensible defaults and ready-made instructions beat reminders.

## Good uses
Writing and improving tests, explaining a failing pipeline log, drafting documentation, summarizing pull requests, cleaning up dependencies, and first drafts of small scripts.

## Not allowed
Pasting secrets, credentials, or customer data into a prompt. Merging AI-written changes without human review. Giving an agent production credentials. Letting an agent change protected files without a platform engineer's approval.

## Rules

| Area | Rule |
|---|---|
| Data and secrets | No secrets or customer data in prompts or in files a tool can read. Use the vendor's content exclusion settings on sensitive repositories. Classify data and say which tools may receive which class. |
| Review | Every change reaches `main` through a pull request reviewed by a person. Reviewers treat AI-written code as untrusted until understood. |
| Agents and tokens | Agents use narrow, short-lived tokens. They may open pull requests but not merge them. No standing admin access. |
| Protected files | Workflow files, deployment configs, and security settings require CODEOWNERS review from the platform team. |
| Pipelines and deploys | Agents never hold production credentials. Anything that deploys goes through environments with required reviewers. Third party actions are pinned to a full commit SHA. |
| Scanning | AI-written code gets the same code scanning, dependency checks, and secret scanning as any other code. |
| Audit and off switch | Keep a record of tool use and agent activity. Be able to turn a tool or feature off quickly. |
| Licensing | Follow the vendor's settings for matching public code, and the company's rules on licenses. |

## Enablement
- **Instructions file** in each repository (for Copilot, `.github/copilot-instructions.md`) with the build and test commands, conventions, and the things not to touch.
- **A short practical guide** with good and bad examples drawn from our own pipelines.
- **A prompt library** of tested prompts, such as "explain this failed run" and "write unit tests for this function".
- **Champions and office hours:** one or two early adopters per team, and a regular drop-in session.
- **A feedback path** for bad suggestions and policy problems, so the rules improve.

## Rollout and measures
1. **Pilot** with two or three willing teams for a few weeks, with the guardrails already on.
2. **Measure** adoption, pull request cycle time, review comments on AI-written code, reverts and defects, and security findings, against a baseline.
3. **Adjust** the rules and the guide, then widen in stages.
4. **Review** this document on a schedule, because the tools change quickly.

## Ownership
The platform team owns this document. Requests for exceptions go through an issue with a named approver and an end date.
