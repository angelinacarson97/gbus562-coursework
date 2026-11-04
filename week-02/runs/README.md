# Runs

This folder holds the four conversations exported from `../prompt-lab.html`
(one per prompting technique). Each file is a Markdown export from the Prompt Lab's
**Export** button: the model name on the first line, then `## System`, `## You`,
and `## Claude` sections.

Planned files (added after running each technique against `claude-sonnet-5`):

| File | Technique |
| --- | --- |
| `run-01-zero-shot.md` | Zero-shot (baseline) |
| `run-02-few-shot.md` | Few-shot examples |
| `run-03-chain-of-thought.md` | Chain-of-thought |
| `run-04-role-prompting.md` | Role / system prompting |

Exports are saved as-is; no API key is ever included in them.
