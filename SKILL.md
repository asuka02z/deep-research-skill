---
name: deep-research-python
description: |
  Generate long-form research reports with structured citations using a Python-driven state machine and the Analyst-Searcher-Writer workflow. Use when: (1) user asks to write a research report, survey, or literature review, (2) user wants deep research on a topic with proper citations, (3) user says "深度调研", "写综述", "写研究报告", "deep research", or "write a survey". NOT for: single-question Q&A, code generation, simple summaries under 500 words, or slide/PPT creation.
---

# Deep Research (Python)

Python state machine controls all deterministic logic (state transitions, citation assignment, validation, report assembly). The agent handles creative tasks (searching, planning, writing) by following action descriptors returned from the Python CLI.

## Setup

- CLI path: `{skill_dir}/agent_cli.py` (where `{skill_dir}` is the directory containing this SKILL.md)
- Working directory: `.deep-research/<run-id>/` under the user's current working directory
  - If the user query starts with `[<custom-id>]` (e.g. `[bench-1]`), strip the bracket prefix from the query and use `<custom-id>` as the run-id
  - Otherwise, derive a short slug from the user query (e.g. `llm-agent-survey`)

## Workflow

### 1. Initialize or Resume

Before starting, check the session directory for existing state:

- **If `<work_dir>/state.json` exists AND `<work_dir>/report.md` does NOT exist** → the session was interrupted. **Resume** it:
  ```
  python {skill_dir}/agent_cli.py next --work-dir <work_dir>
  ```
- **If `<work_dir>/report.md` exists** → the session is already done. Read and present the report.
- **Otherwise** → start a **new** session:
  ```
  python {skill_dir}/agent_cli.py init "<user query>" --work-dir <work_dir>
  ```

IMPORTANT: Never call `init` on a directory that already has `state.json` — it will erase all progress (retrieved data, written content, citations).

### 2. Execute-Complete Loop

Read the returned JSON and execute according to the `action` field:

#### `direct` — Main agent executes directly

The `prompt` field contains instructions for the main agent. Execute the prompt using your own LLM capabilities (do not launch a subagent). Pass the result back via `complete`:

```
python {skill_dir}/agent_cli.py complete <trigger> --work-dir <work_dir> --data '<json>'
```

Where `<trigger>` and `<json>` follow the `on_complete` spec in the action JSON.

**For complex data** (e.g. `init_plan` survey or `extend_plan` expansion with nested JSON, Chinese text, or special characters), write the JSON to a temporary file and use `--data-file` instead to avoid shell quoting issues:

```
python {skill_dir}/agent_cli.py complete <trigger> --work-dir <work_dir> --data-file <path_to_json_file>
```

#### `subagent` — Launch subagent(s)

The `tasks` array contains one or more subagent descriptions. For each task, launch a subagent with the `prompt` from the task and the `description` as a short label.

- If `parallel` is `true`: launch all subagents simultaneously, wait for all to complete
- If `parallel` is `false`: launch a single subagent, wait for completion

After all subagents complete:

```
python {skill_dir}/agent_cli.py complete <trigger> --work-dir <work_dir>
```

The trigger comes from `on_complete.trigger` in the action JSON. No `--data` is needed for subagent actions — the subagents write their results directly to the filesystem.

#### `finalize` — Done

The report has been assembled. Read the `report_path` from the JSON and present the report to the user.

### 3. Repeat

After each `complete` call, the CLI returns the next action JSON. Repeat step 2 until the action JSON contains `"done": true`.

## Action JSON Reference

```json
{
  "done": false,
  "state": "current_state",
  "step": 5,
  "action": "direct | subagent | finalize",
  "prompt": "...",
  "parallel": true,
  "tasks": [{"description": "...", "prompt": "..."}],
  "on_complete": {
    "trigger": "trigger_name",
    "data_spec": {}
  }
}
```

## Notes

- State transitions, citation assignment, and output validation are handled entirely by Python — the agent does not need to manage these
- The `state.json` format is compatible with the original deep-research skill — sessions can be resumed across implementations
- All Python code uses only the standard library (no pip install required)
