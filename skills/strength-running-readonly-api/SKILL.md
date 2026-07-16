---
name: strength-running-readonly-api
description: Reads user-scoped training data, goals, reports, muscle mappings, rest notes, and analysis context from the 力跑双训分析系统 read-only API with curl. Use when an agent needs to inspect this platform's existing data without writing data, synchronizing sources, or triggering analysis.
---

# 力跑双训分析系统只读 API

## When to use

Use this skill to inspect **already stored or already generated** data for the API Key's bound user: data coverage, processed training context, a day's detail, goals, reports, muscle mappings, and rest notes.

Do **not** use it to create or update training data, goals, annotations, mappings, reports, or training plans. This API cannot synchronize sources or trigger platform/LLM analysis.

## Safe setup

The caller must keep these values in its local secret/environment store, not in a prompt, generated response, source file, commit, or log:

```sh
export SRDA_BASE_URL="https://your-platform.example"
export SRDA_API_KEY="<secret issued platform API key>"
```

- `SRDA_BASE_URL` has no trailing `/`.
- `SRDA_API_KEY` is a bearer credential with the `srda_` prefix. It identifies one bound user; never add or infer a `user_id` request parameter.
- The current deployment is still HTTP (not HTTPS). Use a bearer key only for self-use on a trusted network. Do not share keys or expose the service to others until HTTPS (方案 C) is in place.

## Request pattern

Only issue `GET` requests. Keep the credential out of displayed command output whenever possible.

```sh
curl --silent --show-error --fail-with-body \
  -H "Authorization: Bearer ${SRDA_API_KEY}" \
  "${SRDA_BASE_URL}/api/v1/meta"
```

Success has this envelope:

```json
{"ok": true, "data": {}}
```

API failures have this envelope; `code` is a numeric HTTP status:

```json
{"ok": false, "error": {"code": 401, "message": "..."}}
```

## Available reads

| GET route | Read |
|---|---|
| `/api/v1/meta` | data coverage and synchronization metadata |
| `/api/v1/context` | processed cross-training context for analysis |
| `/api/v1/days/{YYYY-MM-DD}` | strength and running detail for one date |
| `/api/v1/goals/current` | current goal configuration |
| `/api/v1/goals/history` | goal-version history |
| `/api/v1/reports` | historical report summaries |
| `/api/v1/reports/{report_id}` | one complete stored report snapshot |
| `/api/v1/muscle-map` | action-to-muscle mappings |
| `/api/v1/rest-notes` | recovery/interruption annotations |

Read [LLM-INSTRUCTIONS.md](LLM-INSTRUCTIONS.md) before use for all curl recipes, date parameters, expected failures, interpretation rules, and credential handling.

## Non-negotiable boundaries

1. Never make `POST`, `PUT`, `PATCH`, or `DELETE` requests to `/api/v1/*`.
2. Never attempt writes, sync, reanalysis, report generation, or any platform LLM invocation.
3. Never reveal, store, log, or repeat the API key or the `Authorization` header.
4. Treat every response as private data belonging only to the Key's bound user.
5. Treat reports as historical snapshots; state uncertainty when source data is missing rather than inventing facts.

## Authoritative references

- Implementation: [app/api_v1.py](../../app/api_v1.py)
- Key lifecycle and per-user isolation: [app/api_keys.py](../../app/api_keys.py)
- API contract and HTTP/HTTPS warning: [docs/design/api-design.md §12](../../docs/design/api-design.md#L423-L451)
- Architectural boundary: [ADR 0004](../../docs/adr/0004-outbound-api-reserved-readonly.md)
