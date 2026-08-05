# Tasks

For the planned C++ appliance and seven-day structured synchronization
contract, see [Nextcloud MCP sync for a C++ appliance](nextcloud-appliance-sync.md).

Nextcloud is the default task backend used by the agent. The original SQLite
task manager (`data/tasks.db`) remains available when the user explicitly asks
for a local task.

## Tools

### list_nextcloud_events
Lists Nextcloud calendar events for one local day. With no date, it reads the
exact current time at execution using `NC_TIMEZONE` and queries today. Timed
events are labeled `ended`, `ongoing`, or `upcoming`; all-day events are also
included. Recurring events are expanded by CalDAV only for the requested day.

The result is bounded to at most 20 events, 120 description characters per
event, and 4096 characters total. Raw CalDAV and iCalendar data is never sent
to the model.

| Param | Type | Default |
|-------|------|---------|
| `date` | string or null | today; explicit values use `YYYY-MM-DD` |
| `limit` | int | `10` (maximum `20`) |

### list_nextcloud_tasks
Lists pending Nextcloud tasks by default. The result is bounded to protect the
agent context: at most 20 tasks, 120 description characters per task, and 4096
characters total. Raw CalDAV and iCalendar data is never returned to the model.

| Param | Type | Default |
|-------|------|---------|
| `show_completed` | bool | `false` |
| `limit` | int | `10` (maximum `20`) |
| `calendar` | string or null | all task lists; exact display name or slug |

Requests for "all tasks" or "todas as tasks" use `show_completed=false` with a
limit of 20: all incomplete tasks are considered, including overdue tasks, but
completed tasks remain excluded. `show_completed=true` is used only when the
user explicitly asks for completed tasks or task history. The response reports
the total count and omitted entries instead of sending the full history into
the model context.

### create_nextcloud_task
Creates a `VTODO` containing its own `VALARM` plus a linked transparent
`VEVENT` with another `VALARM`. The VTODO alarm makes the reminder visible on
the task. The event alarm provides server-side notification delivery on
Nextcloud installations that do not schedule VTODO alarms.

The Nextcloud Notifications app and background jobs/cron must be working for
the server to deliver the event alarm. The linked event is not automatically
removed if the task is completed or deleted directly in another Nextcloud
client.

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `title` | string | yes | |
| `due_at` | string | yes | Local time, `YYYY-MM-DD HH:MM:SS` |
| `description` | string | no | |
| `reminder_minutes_before` | int | no | Default `0`, at due time |
| `calendar` | string | no | Exact destination task-list name or slug |

### set_nextcloud_task_reminder
Adds or replaces the Servitor-managed reminder on an existing task. It updates
both the task-visible `VTODO` alarm and the linked transparent `VEVENT` alarm.
The task must have a due date and the calculated reminder time must be in the
future.

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `task` | string | yes | Exact title, full UID, or short UID |
| `reminder_minutes_before` | int | yes | `0` means at due time |
| `calendar` | string | no | Limit matching to one task list |

### complete_nextcloud_task
Marks one existing Nextcloud `VTODO` as completed. The selector must be an
exact title, full UID, or short UID returned by `list_nextcloud_tasks`.
Ambiguous titles are rejected instead of updating multiple tasks.

The tool fetches the complete iCalendar resource, updates only the matching
`VTODO`, and writes it back with `If-Match` using the CalDAV `ETag`. A
concurrent change therefore fails safely instead of being overwritten.

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `task` | string | yes | Exact title, full UID, or short UID |
| `calendar` | string | no | Limit matching to one task list |

### get_nextcloud_task
Returns the full bounded details for one task, including its task list, status,
description, due date, categories, priority, and available timestamps.

### update_nextcloud_task
Updates only the supplied fields. `due_at` uses local
`YYYY-MM-DD HH:MM:SS`; an empty value clears the due date. Status accepts
`needs-action`, `in-process`, `completed`, or `cancelled`. Setting
`needs-action` reopens a completed task. Writes use `If-Match` with the current
CalDAV `ETag`.

### delete_nextcloud_task
Permanently deletes one exact task with an `ETag` precondition. The agent must
use this only after an explicit deletion request.

### move_nextcloud_task
Moves one exact task between writable Nextcloud task lists using WebDAV
`MOVE`, `If-Match`, and `Overwrite: F`. `destination_calendar` is required;
the optional `calendar` parameter scopes the source list.

Configuration is loaded from the repository-root `.env` using
`python-dotenv`. Required names are `NC_URL`, `NC_USER`, and
`NC_APP_PASSWORD`. `NC_TIMEZONE` defaults to `America/Recife`.
`NC_TASK_CALENDAR` and `NC_REMINDER_CALENDAR` are optional selectors for
accounts with ambiguous calendar choices. TLS verification uses the operating
system trust store; `NC_CA_BUNDLE` can point to a private CA file when needed.
For a trusted private network with a self-signed certificate,
`NC_TLS_VERIFY=false` disables certificate verification explicitly. Installing
the private CA and keeping verification enabled is preferred.

## Local SQLite Tools

### create_local_task
| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `title` | string | yes | |
| `description` | string | no | |
| `due_at` | string | no | Format: `YYYY-MM-DD HH:MM:SS` |
| `recurrence_type` | string | no | `none` / `daily` / `weekly` / `monthly` |
| `recurrence_interval` | int | no | e.g. `2` = every 2 days |
| `recurrence_day_of_week` | int | no | `0`=Sun … `6`=Sat |
| `recurrence_day_of_month` | int | no | `1`–`31` |
| `timezone` | string | no | default `America/Recife` |

### list_local_tasks
| Param | Type | Default |
|-------|------|---------|
| `show_completed` | bool | `false` |
| `limit` | int | `20` |

### get_local_task
| Param | Type | Required |
|-------|------|----------|
| `task_id` | int | yes |

### update_local_task
`task_id` required. All other fields optional — only provided ones are updated.

### complete_local_task
Marks a task done. If recurring, automatically creates the next occurrence.

| Param | Type | Required |
|-------|------|----------|
| `task_id` | int | yes |

### delete_local_task
Permanently removes a task.

| Param | Type | Required |
|-------|------|----------|
| `task_id` | int | yes |

## Example prompts

- "Create a task to buy groceries tomorrow at 10am"
- "Remind me 30 minutes before the server maintenance tomorrow at 10am"
- "List my tasks"
- "List my Nextcloud tasks in TrabalhoFNDE"
- "Move task abc12345 to TrabalhoFNDE"
- "Reopen task abc12345"
- "Mark the Nextcloud task Curso Onshape as done"
- "Look at my calendar for today and tell me what is next"
- "Complete local task 3"
- "Delete local task 5"
