---
applyTo: "src/sphereloom/tools/**/*.py"
---

# MCP tool instructions

- Name tools in `snake_case`.
- Use `<noun>_<verb>` grouping, for example `camera_status`, `camera_take_photo`, `files_list`, `files_download`, `jobs_list`, `jobs_get`, and `jobs_cancel`.
- Keep one coherent MCP tool surface across all backends.
- Do not expose separate OSC, USB, or Media SDK tool families unless an approved spec requires it.
- Every tool must have a pydantic input model.
- Every tool must have a pydantic output model.
- Every tool must have a description written for an LLM consumer that explains when to use it and what it returns.
- Mark read-only tools as read-only when the MCP SDK supports annotations for that metadata.
- Document idempotency expectations in descriptions and model fields.
- Never return binary blobs from a tool.
- Return workspace-relative paths plus metadata for media and exported files.
- Use `cursor`, `limit`, and `next_cursor` for pagination.
- Validate `limit` with a safe maximum.
- Keep cursors opaque to clients.
- Use the public structured error envelope for tool failures.
- Include `code`, `message`, and relevant context fields in errors.
- Unsupported capabilities must return `{code: "unsupported", backend, reason, docs_url}`.
- Check the capability registry before dispatching to an adapter.
- Do not let adapter-specific exceptions leak through the MCP boundary.
- Destructive tools such as delete and format require a confirmation token.
- Confirmation-token flows must be two-step and expire after the configured TTL.
- Tools that can exceed a few seconds must either enforce explicit timeouts or promote work to a stateful job.
- Long-running tools return a `job_id` instead of waiting for completion.
- Job management goes through `jobs_list`, `jobs_get`, and `jobs_cancel`.
- Make cancellation behavior explicit in the output model.
- Do not claim live preview, streaming, OSC exposure control, or OSC video stitching support.
- For planned-but-unavailable tools, return the structured `unsupported` error honestly.
