# Tasks

Task management backed by SQLite (`data/tasks.db`). Supports one-off and recurring tasks.

## Tools

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
- "Remind me every Monday to check server logs"
- "List my tasks"
- "Complete task 3"
- "Delete task 5"
