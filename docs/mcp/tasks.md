# Tasks

Nextcloud is the default task backend used by the agent. The original SQLite
task manager (`data/tasks.db`) remains available when the user explicitly asks
for a local task.

## Tools

### list_nextcloud_tasks
Lists pending Nextcloud tasks by default. The result is bounded to protect the
agent context: at most 20 tasks, 120 description characters per task, and 4096
characters total. Raw CalDAV and iCalendar data is never returned to the model.

| Param | Type | Default |
|-------|------|---------|
| `show_completed` | bool | `false` |
| `limit` | int | `10` (maximum `20`) |

### create_nextcloud_task
Creates both a `VTODO` for Nextcloud Tasks and a linked transparent `VEVENT`
with `VALARM`. The event is required because Nextcloud 33 only schedules
server-side reminders for `VEVENT`, not `VTODO`.

The Nextcloud Notifications app and background jobs/cron must be working for
the server to deliver the event alarm. The linked event is not automatically
removed if the task is completed or deleted directly in another Nextcloud
client; this integration currently supports Nextcloud list and create only.

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `title` | string | yes | |
| `due_at` | string | yes | Local time, `YYYY-MM-DD HH:MM:SS` |
| `description` | string | no | |
| `reminder_minutes_before` | int | no | Default `0`, at due time |

Configuration is loaded from the repository-root `.env` using
`python-dotenv`. Required names are `NC_URL`, `NC_USER`, and
`NC_APP_PASSWORD`. `NC_TIMEZONE` defaults to `America/Recife`.
`NC_TASK_CALENDAR` and `NC_REMINDER_CALENDAR` are optional selectors for
accounts with ambiguous calendar choices. TLS verification uses the operating
system trust store; `NC_CA_BUNDLE` can point to a private CA file when needed.

## Local SQLite Tools

### create_task
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

### list_tasks
| Param | Type | Default |
|-------|------|---------|
| `show_completed` | bool | `false` |
| `limit` | int | `20` |

### get_task
| Param | Type | Required |
|-------|------|----------|
| `task_id` | int | yes |

### update_task
`task_id` required. All other fields optional — only provided ones are updated.

### complete_task
Marks a task done. If recurring, automatically creates the next occurrence.

| Param | Type | Required |
|-------|------|----------|
| `task_id` | int | yes |

### delete_task
Permanently removes a task.

| Param | Type | Required |
|-------|------|----------|
| `task_id` | int | yes |

## Example prompts

- "Create a task to buy groceries tomorrow at 10am"
- "Remind me 30 minutes before the server maintenance tomorrow at 10am"
- "List my tasks"
- "Complete local task 3"
- "Delete local task 5"
