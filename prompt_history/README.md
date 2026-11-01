# Prompt history

This directory preserves the prompts that drove the FleetMind build. It has two layers:

1. **Verbatim record** (`verbatim/`) - user-authored prompts recovered from the local OpenCode session
   database, with session identity, timestamps, and message IDs. The text is not paraphrased.
2. **Supplementary summaries** (`01-*.md` through `08-*.md`) - normalized engineering summaries that group the
   work by theme. They are useful context but are **not** verbatim transcripts and must not be cited as quotes.

## Evidence source and extraction

| Property | Value |
|---|---|
| Tool | OpenCode |
| Database | `~/.local/share/opencode/opencode.db` |
| Tables read | `session_v2`, `session_message` |
| Filter | sessions whose `directory` is `F:/Projects/FleetMind` |
| Message role | `user` only |
| Extraction date | 2026-11-01 |
| Machine-readable export | [`verbatim/opencode-prompts.json`](verbatim/opencode-prompts.json) |

The exporter read the stored message payloads without rewriting them. Surrounding headings, separators, and
metadata were added in the Markdown renderings. Between the `BEGIN VERBATIM PROMPT` and `END VERBATIM PROMPT`
markers, the stored text is reproduced exactly; one blank line after the opening marker and one newline before
the closing marker are added only for Markdown readability.

## Coverage

| Session | Title | Created (UTC) | User messages | Verbatim file |
|---|---|---|---:|---|
| `ses_e6ec26dacffe4AXJec6X5zMPrp` | FleetMind delivery planning platform | 2026-10-31T09:14:53Z | 9 | [`verbatim/opencode/2026-10-31-fleetmind-delivery-planning-platform.md`](verbatim/opencode/2026-10-31-fleetmind-delivery-planning-platform.md) |
| `ses_e6a739457ffekis2E59zyXjhlU` | (untitled) | 2026-11-01T05:19:30Z | 2 | [`verbatim/opencode/2026-11-01-context7-enotfound.md`](verbatim/opencode/2026-11-01-context7-enotfound.md) |
| `ses_e6a6f1415ffeFXwHVESaGRmFaD` | OpenCode context7 ENOTFOUND | 2026-11-01T05:24:25Z | 1 | [`verbatim/opencode/2026-11-01-context7-enotfound.md`](verbatim/opencode/2026-11-01-context7-enotfound.md) |

The main session contains the build prompt and every later audit/remediation instruction:

| # | UTC timestamp | Characters | Prompt |
|---:|---|---:|---|
| 1 | 2026-10-31T09:14:54Z | 14,988 | Master engineering execution prompt |
| 2 | 2026-10-31T11:55:37Z | 8 | `continue` |
| 3 | 2026-10-31T15:44:11Z | 4,737 | Final independent engineering audit |
| 4 | 2026-10-31T16:55:43Z | 6,813 | Post-audit correction and finalization |
| 5 | 2026-11-01T05:32:37Z | 8 | `continue` |
| 6 | 2026-11-01T06:03:22Z | 1,874 | Read-only Git history and public-release security audit |
| 7 | 2026-11-01T07:51:12Z | 9,018 | Final release readiness and shipability audit |
| 8 | 2026-11-01T11:26:48Z | 9,948 | Final audit remediation (Phase 8) |
| 9 | 2026-11-01T12:12:05Z | 8 | `continue` |

The extraction cutoff is the last captured prompt at 2026-11-01T12:12:05Z. Prompts added to the local database after that
cutoff are not part of this record.

The two auxiliary sessions each contain the same 460-character OpenCode context7 MCP failure notice; the
first also contains one `continue`. They are not part of the FleetMind engineering conversation but are
included so the record is complete for this working directory.

## What is not covered

- **Other AI tools.** No ChatGPT, Claude, Codex, Copilot, or other assistant export was found for this
  project. If those tools were used outside the local OpenCode database, their sessions are not represented
  here and are not reconstructed.
- **Deleted or cleared sessions.** The local database contains only the three sessions listed above. Earlier
  OpenCode sessions that were deleted or overwritten cannot be recovered from this source.
- **Assistant output and tool calls.** Only user prompts are published. Assistant responses, reasoning,
  command output, and tool transcripts are deliberately excluded.
- **Non-user events.** System reminders, checkpoints, and injected harness instructions are not user prompts
  and are not reproduced.

## Privacy review

The published prompts are engineering instructions. Before publication they were checked for API keys,
tokens, passwords, connection strings, private keys, and personal data. None were found. The prompts discuss
security review criteria and secret-scanning rules but do not contain credentials.

## How to reproduce

The authoritative machine-readable export is [`verbatim/opencode-prompts.json`](verbatim/opencode-prompts.json).
It records the tool, session ID, working directory, model, timestamps, message IDs, stored sequence numbers,
and exact text. The Markdown files in `verbatim/opencode/` are human-readable renderings of the same records.
