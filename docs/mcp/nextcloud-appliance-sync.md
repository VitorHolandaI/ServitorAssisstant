# Nextcloud MCP sync for a C++ appliance

## Goal

This document describes how a C++ appliance can synchronize Nextcloud tasks
and calendar events through the Servitor MCP server. The appliance requirement
is a seven-day snapshot starting on the local calendar date on which its clock
is synchronized.

The sync interval is defined as a half-open range:

```text
[local day at 00:00:00, local day + 7 days at 00:00:00)
```

For example, if the appliance synchronizes its clock at any time on
`2026-08-04` in `America/Recife`, the snapshot covers:

```text
2026-08-04 00:00:00 -03 <= item < 2026-08-11 00:00:00 -03
```

This means seven local calendar days, including the date of the clock sync.
The end instant is exclusive.

## Recommendation

The appliance should call the MCP server directly. It should not send a
natural-language prompt to the LLM agent and should not parse the agent's
human-readable response. An embedded sync process needs deterministic JSON,
stable identifiers, explicit timezone data, and complete snapshot semantics.

Although this operation is conceptually a data "GET", MCP tool invocation is
an HTTP `POST` containing a JSON-RPC `tools/call` request. Do not implement it
as an HTTP `GET /tasks` request unless a separate REST API is added later.

The current MCP tools are designed primarily for an LLM and return bounded
text. A dedicated structured sync tool should be added before implementing the
production appliance:

```text
sync_nextcloud_agenda(start_date=null, days=7,
                      include_overdue_tasks=true,
                      include_undated_tasks=false)
```

Until that tool exists, a prototype can call `list_nextcloud_events` once for
each of the seven dates and call `list_nextcloud_tasks` separately. This is not
recommended for production because the current tools limit results to 20
items, return text, and do not provide snapshot deletion semantics.

## Existing server

The MCP Streamable HTTP endpoint is:

```text
http://SERVER_IP:8001/mcp
```

Relevant implementation files:

- `api/mcp_module/stremable_http/stream2.py`: MCP tool registration.
- `api/mcp_module/stremable_http/nextcloud_tasks.py`: CalDAV discovery,
  parsing, CRUD, reminders, event expansion, and timezone handling.
- `api/server/Server.py`: LLM prompt and tool-selection policy. The appliance
  does not need this layer.

Current Nextcloud tools:

| Tool | Purpose |
|------|---------|
| `list_nextcloud_events` | Events for one local day; recurring events expanded |
| `list_nextcloud_tasks` | Bounded task list, optionally filtered by task list |
| `get_nextcloud_task` | Full details for one task |
| `create_nextcloud_task` | Create task and reminders |
| `set_nextcloud_task_reminder` | Add/update a task reminder |
| `update_nextcloud_task` | Update title, description, due date, or status |
| `complete_nextcloud_task` | Complete a task |
| `move_nextcloud_task` | Move a task between Nextcloud task lists |
| `delete_nextcloud_task` | Permanently delete a task |

The server discovers CalDAV collections instead of hard-coding them. Existing
`VTODO` lists include `Tasks`, `LinkedInPost`, `Aprender`, `ProjetosList`,
`NPU_PROJECTS`, `CursoFoco`, and `TrabalhoFNDE`. Calendar names must not be
compiled into appliance firmware because users can rename or add collections.

## MCP request format

The deployed server accepts JSON-RPC requests over Streamable HTTP. Requests
must include both accepted response media types because MCP can respond as an
SSE stream:

```http
POST /mcp HTTP/1.1
Content-Type: application/json
Accept: application/json, text/event-stream
```

Example tool call:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "list_nextcloud_events",
    "arguments": {
      "date": "2026-08-04",
      "limit": 20
    }
  }
}
```

The current server commonly responds as SSE:

```text
event: message
data: {"jsonrpc":"2.0","id":1,"result":{...}}
```

The C++ client must parse SSE framing and then parse the JSON from each `data:`
line. It must match the JSON-RPC `id` and inspect `result.isError`. Do not treat
HTTP 200 alone as tool success.

For protocol compatibility, a general MCP client should perform `initialize`
and then `tools/list` before calling tools. The deployed stateless endpoint has
also accepted direct `tools/call` requests, but the initialization flow is the
safer long-term contract.

## Required seven-day sync tool

### Input

Recommended MCP input schema:

```json
{
  "start_date": "2026-08-04",
  "days": 7,
  "include_overdue_tasks": true,
  "include_undated_tasks": false,
  "task_lists": null,
  "event_calendars": null
}
```

Rules:

- `start_date=null` means the current date in `NC_TIMEZONE` at tool execution.
- An explicit date uses `YYYY-MM-DD`.
- `days` should default to `7` and be bounded, for example `1..31`.
- `task_lists=null` and `event_calendars=null` mean all compatible CalDAV
  collections.
- Collection filters use exact display names or CalDAV slugs.

### Structured output

The tool should return a JSON object, not a formatted string:

```json
{
  "schema_version": 1,
  "snapshot_id": "2026-08-04T19:00:00Z/uuid",
  "generated_at": "2026-08-04T19:00:00Z",
  "timezone": "America/Recife",
  "range": {
    "start_local": "2026-08-04T00:00:00-03:00",
    "end_local_exclusive": "2026-08-11T00:00:00-03:00",
    "days": 7
  },
  "events": [
    {
      "key": "Personal/event-uid/2026-08-05T14:00:00Z",
      "uid": "event-uid",
      "recurrence_id": "2026-08-05T14:00:00Z",
      "calendar": "Personal",
      "title": "Meeting",
      "description": "",
      "location": "",
      "start": "2026-08-05T14:00:00Z",
      "end": "2026-08-05T15:00:00Z",
      "all_day": false,
      "status": "CONFIRMED",
      "last_modified": "2026-08-01T10:00:00Z"
    }
  ],
  "tasks": [
    {
      "key": "TrabalhoFNDE/task-uid",
      "uid": "task-uid",
      "list": "TrabalhoFNDE",
      "title": "Review document",
      "description": "",
      "due": "2026-08-06T17:00:00Z",
      "status": "NEEDS-ACTION",
      "percent_complete": 0,
      "priority": null,
      "categories": [],
      "overdue": false,
      "last_modified": "2026-08-02T09:00:00Z"
    }
  ],
  "counts": {
    "events": 1,
    "tasks": 1,
    "overdue_tasks": 0,
    "undated_tasks": 0
  },
  "complete": true,
  "errors": []
}
```

`complete=true` means every requested collection was read successfully and the
snapshot can be used to remove local records that disappeared from Nextcloud.
If one collection fails, return `complete=false` and include a bounded error;
the appliance must retain unseen local records during an incomplete snapshot.

## Calendar query semantics

The server should issue a CalDAV `calendar-query` for one seven-day range, not
seven unrelated requests. The local range boundaries must be converted to UTC
for the CalDAV `time-range`.

The request needs server-side recurrence expansion:

```xml
<c:calendar-data>
  <c:expand start="20260804T030000Z" end="20260811T030000Z"/>
</c:calendar-data>
```

Important event rules:

- Include an event when it overlaps the range, not only when it starts in the
  range. This includes an event that began before midnight and ends inside the
  range.
- Recurring events must be expanded only inside the requested interval.
- A recurrence instance is identified by calendar, UID, and recurrence ID.
- For a non-recurring event, use calendar, UID, and start time as the stable
  instance key.
- `DTEND` for an all-day event is exclusive. An all-day event from August 4 to
  August 5 has `DTSTART=20260804` and `DTEND=20260805`.
- Ignore `STATUS:CANCELLED` instances, or return them as tombstones if the
  appliance performs incremental synchronization.
- Store instants in UTC. Convert to the configured timezone only for display.

## Task query semantics

Tasks are `VTODO` resources stored in CalDAV task-list collections.

For a seven-day appliance view, divide incomplete tasks into explicit groups:

1. Due in the snapshot range.
2. Overdue before the range start, when `include_overdue_tasks=true`.
3. Without a due date, when `include_undated_tasks=true`.

Do not silently mix completed tasks into the active snapshot. A separate
option can include task history when needed.

Task identity is the pair `(list, UID)`. A move between lists changes that
pair, so the appliance should also retain the raw UID to recognize a move as
the same logical task when desired.

The current server uses these status values:

- `NEEDS-ACTION`
- `IN-PROCESS`
- `COMPLETED`
- `CANCELLED`

Reminders are represented twice by design:

- A `VALARM` inside the `VTODO`, visible as the task's reminder.
- A linked transparent `VEVENT` with `VALARM`, used for reliable server-side
  notification delivery.

The linked event contains `X-SERVITOR-TASK-UID`. A consumer that displays both
tasks and calendar events should normally hide events containing this property
or label them as task reminders to avoid showing duplicate user-facing items.

## Appliance synchronization algorithm

Recommended full-snapshot algorithm:

1. Synchronize the appliance clock through its trusted time source.
2. Determine the local date using the timezone returned/configured by the
   server. Do not derive the date from UTC alone.
3. Call `sync_nextcloud_agenda` with that date and `days=7`.
4. Validate HTTP, SSE, JSON-RPC, MCP tool status, `schema_version`, range, and
   `complete`.
5. Start a local database transaction.
6. Mark existing records in the requested scope as unseen.
7. Upsert every event and task by its stable key and mark it seen.
8. If `complete=true`, delete or archive records that remain unseen.
9. Commit the local transaction.
10. Record `snapshot_id`, `generated_at`, and the successful clock-sync time.

On any network, parse, or incomplete-snapshot failure:

- Keep the last successful snapshot.
- Do not erase unseen records.
- Retry with bounded exponential backoff.
- Expose the age of the last successful sync in the appliance UI.

The first implementation can perform a full seven-day snapshot on each clock
sync. CalDAV sync tokens can be added later if bandwidth becomes a measured
problem; they are not required for a small seven-day window.

## C++ implementation notes

The C++ standard library does not provide an HTTP client or JSON parser. A
typical implementation uses:

- an HTTP/TLS library such as libcurl;
- a JSON parser;
- SQLite or another transactional local store;
- the platform timezone database, or UTC values plus the server-provided
  offset for a constrained display.

The MCP response parser must support both:

- `application/json` response bodies;
- `text/event-stream` responses with `event:` and `data:` fields.

Use these timeout classes separately:

- connection timeout;
- complete request timeout;
- maximum response size.

Never parse the existing human-readable task/event lines into appliance data.
Their wording and truncation are intentionally optimized for LLM context, not
machine synchronization.

## Security

The current MCP server listens on `0.0.0.0:8001`. The appliance must reach it
only over a trusted private network or VPN until MCP authentication and TLS are
configured at a reverse proxy. Do not expose port 8001 directly to the public
internet.

The appliance should not receive or store `NC_APP_PASSWORD`. Nextcloud
credentials stay on the Servitor server, which acts as the CalDAV gateway.

For a production deployment, add:

- TLS between appliance and MCP endpoint;
- appliance authentication;
- authorization limiting it to read-only sync tools unless writes are needed;
- replay/rate limits;
- bounded request and response sizes;
- audit logs without task descriptions or credentials when privacy requires
  it.

## Implementation checklist

- [ ] Add `sync_nextcloud_agenda` with structured output.
- [ ] Query one seven-day event range with recurrence expansion.
- [ ] Filter tasks into due, overdue, and undated groups.
- [ ] Hide or identify Servitor linked reminder events.
- [ ] Return `complete=false` on partial collection failures.
- [ ] Add tests for DST/range boundaries, all-day events, overlap, recurring
      events, moved tasks, cancellation, partial failure, and output bounds.
- [ ] Add MCP authentication/TLS before untrusted network deployment.
- [ ] Implement the C++ snapshot transaction and stale-data behavior.

## Standards

- RFC 5545: iCalendar (`VEVENT`, `VTODO`, `VALARM`, recurrence, all-day end
  exclusivity).
- RFC 4791: CalDAV access and `calendar-query`.
- MCP Streamable HTTP transport and JSON-RPC tool calls.
