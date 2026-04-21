from mcp.server.fastmcp import FastMCP
import openmeteo_requests
import requests_cache
from retry_requests import retry
import pandas as pd
import sqlite3
import datetime
import os
import socket
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "tasks.db"
DB_PATH.parent.mkdir(exist_ok=True)

load_dotenv(Path(__file__).parent.parent.parent / ".env")

mcp = FastMCP("GeneralAssistant", host="0.0.0.0", port=8001, stateless_http=True)


# ── SQLite helpers ──────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                created_at DATETIME NOT NULL,
                due_at DATETIME,
                is_completed BOOLEAN DEFAULT 0,
                recurrence_type TEXT DEFAULT 'none',
                recurrence_interval INTEGER DEFAULT 1,
                recurrence_day_of_week INTEGER,
                recurrence_day_of_month INTEGER,
                timezone TEXT DEFAULT 'America/Recife'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME NOT NULL
            )
        """)
        conn.commit()


init_db()
print(f"[MCP] DB initialized at {DB_PATH}")


# ── Weather tools ───────────────────────────────────────────────

cache_session = requests_cache.CachedSession('.cache', expire_after=3600)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)


@mcp.tool()
async def default_response(description="CALL THIS TOOL WHEN NO TOOL EXACTLY MATCHES WHAT THE USER WANTS") -> str:
    return "there is no tool to call, so you must now only respond the user question"


@mcp.tool()
async def help() -> str:
    """Returns a full reference of all available tools, their parameters, and which are required."""
    return """
=== MCP Server: GeneralAssistant ===

── MATH ────────────────────────────────────────────
add_numbers(a*, b*)          — Add two numbers
subtract_numbers(a*, b*)     — Subtract b from a
multiply_numbers(a*, b*)     — Multiply a × b
divide_numbers(a*, b*)       — Divide a ÷ b (error if b=0)
  * a, b: float, REQUIRED

── WEATHER ─────────────────────────────────────────
get_forecast(latitude?, longitude?)
  — Current weather for Campina Grande (or custom coords)
  latitude:  float, optional (default -7.2307, Campina Grande)
  longitude: float, optional (default -35.8816, Campina Grande)
  Returns: temperature, humidity, rain, wind speed

── TASKS ───────────────────────────────────────────
create_task(title*, description?, due_at?, recurrence_type?, recurrence_interval?, recurrence_day_of_week?, recurrence_day_of_month?, timezone?)
  title:                  string, REQUIRED
  description:            string, optional
  due_at:                 string, optional — format: 'YYYY-MM-DD HH:MM:SS'
  recurrence_type:        string, optional — 'none' | 'daily' | 'weekly' | 'monthly' (default: 'none')
  recurrence_interval:    int,    optional — e.g. 2 = every 2 days/weeks/months (default: 1)
  recurrence_day_of_week: int,    optional — 0=Sun, 1=Mon, ..., 6=Sat (weekly recurrence)
  recurrence_day_of_month:int,    optional — 1–31 (monthly recurrence)
  timezone:               string, optional (default: 'America/Recife')

list_tasks(show_completed?, limit?)
  show_completed: bool, optional — include done tasks (default: False)
  limit:          int,  optional — max results (default: 20)

get_task(task_id*)
  task_id: int, REQUIRED — ID of the task

update_task(task_id*, title?, description?, due_at?, recurrence_type?, recurrence_interval?, recurrence_day_of_week?, recurrence_day_of_month?, timezone?)
  task_id: int, REQUIRED — only provided fields are updated

complete_task(task_id*)
  task_id: int, REQUIRED — marks done; if recurring, auto-creates next occurrence

delete_task(task_id*)
  task_id: int, REQUIRED — permanently deletes task

── HELP ────────────────────────────────────────────
help()         — this reference
task_help()    — detailed task-only reference with examples
"""


@mcp.tool("add_numbers", description="Add two numbers and return the result.")
async def add_numbers(a: float, b: float):
    return a + b


@mcp.tool("subtract_numbers", description="Subtract the second number from the first.")
async def subtract_numbers(a: float, b: float):
    return a - b


@mcp.tool("multiply_numbers", description="Multiply two numbers together.")
async def multiply_numbers(a: float, b: float):
    return a * b


@mcp.tool("divide_numbers", description="Divide the first number by the second.")
async def divide_numbers(a: float, b: float):
    if b == 0:
        return "Error: division by zero."
    return a / b


@mcp.tool()
async def get_forecast(latitude: float = -7.23071810, longitude: float = -35.88166640) -> str:
    """Get weather forecast. Call with NO arguments for Campina Grande, Paraíba (default location). Only pass coordinates if the user explicitly asks about a different city."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": ["temperature_2m", "relative_humidity_2m", "rain", "wind_speed_10m"],
        "forecast_days": 1,
        "timezone": "America/Recife",
    }

    try:
        responses = openmeteo.weather_api(url, params=params)
        response = responses[0]

        hourly = response.Hourly()
        hourly_temperature = hourly.Variables(0).ValuesAsNumpy()
        hourly_humidity = hourly.Variables(1).ValuesAsNumpy()
        hourly_rain = hourly.Variables(2).ValuesAsNumpy()
        hourly_wind = hourly.Variables(3).ValuesAsNumpy()

        hourly_data = {"date": pd.date_range(
            start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
            end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
            freq=pd.Timedelta(seconds=hourly.Interval()),
            inclusive="left"
        )}
        hourly_data["temperature_2m"] = hourly_temperature
        hourly_data["relative_humidity_2m"] = hourly_humidity
        hourly_data["rain"] = hourly_rain
        hourly_data["wind_speed_10m"] = hourly_wind

        df = pd.DataFrame(data=hourly_data)
        now_utc = pd.Timestamp.now(tz="UTC")
        closest_idx = (df["date"] - now_utc).abs().argmin()

        row = df.iloc[closest_idx]
        return (
            f"Campina Grande, Paraiba - Current Weather:\n"
            f"Temperature: {row['temperature_2m']:.1f}°C\n"
            f"Humidity: {row['relative_humidity_2m']:.0f}%\n"
            f"Rain: {row['rain']:.1f} mm\n"
            f"Wind: {row['wind_speed_10m']:.1f} km/h"
        )
    except Exception as e:
        return f"Unable to fetch weather data: {e}"


# ── Task tools ──────────────────────────────────────────────────

@mcp.tool()
async def task_help() -> str:
    """Returns info about all available task management commands. Call this when the user asks what they can do with tasks."""
    return (
        "Task Manager - Available commands:\n\n"
        "1. create_task(title, description, due_at, recurrence_type, recurrence_interval, ...)\n"
        "   - Creates a new task. due_at format: 'YYYY-MM-DD HH:MM:SS'\n"
        "   - recurrence_type: 'none', 'daily', 'weekly', 'monthly'\n"
        "   - recurrence_interval: e.g. 2 means every 2 days/weeks/months\n"
        "   - recurrence_day_of_week: 0=Sun, 1=Mon, ..., 6=Sat\n"
        "   - recurrence_day_of_month: 1-31\n\n"
        "2. list_tasks(show_completed=False, limit=20)\n"
        "   - Lists tasks. By default only shows incomplete ones.\n\n"
        "3. get_task(task_id)\n"
        "   - Shows full details of a single task.\n\n"
        "4. update_task(task_id, title, description, due_at, ...)\n"
        "   - Updates any field of an existing task.\n\n"
        "5. complete_task(task_id)\n"
        "   - Marks a task as done. If recurring, auto-creates the next one.\n\n"
        "6. delete_task(task_id)\n"
        "   - Permanently deletes a task.\n\n"
        "Examples:\n"
        "  - 'Create a task to buy groceries tomorrow at 10am'\n"
        "  - 'Create a daily recurring task to check server logs'\n"
        "  - 'List my tasks'\n"
        "  - 'Complete task 1'"
    )


@mcp.tool()
async def create_task(
    title: str,
    description: str = None,
    due_at: str = None,
    recurrence_type: str = 'none',
    recurrence_interval: int = 1,
    recurrence_day_of_week: int = None,
    recurrence_day_of_month: int = None,
    timezone: str = 'America/Recife'
) -> str:
    """
    Creates a new task.
    :param title: The title of the task.
    :param description: Optional details about the task.
    :param due_at: The due date and time in 'YYYY-MM-DD HH:MM:SS' format.
    :param recurrence_type: 'none', 'daily', 'weekly', 'monthly'.
    :param recurrence_interval: Interval for recurrence (e.g., every 2 days).
    :param recurrence_day_of_week: Day of the week for weekly recurrence (0-6, Sun-Sat).
    :param recurrence_day_of_month: Day of the month for monthly recurrence (1-31).
    :param timezone: The timezone for the task.
    """
    print(f"[MCP] create_task: {title}")
    with get_db() as conn:
        created_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor = conn.execute(
            """INSERT INTO tasks (title, description, created_at, due_at, recurrence_type,
               recurrence_interval, recurrence_day_of_week, recurrence_day_of_month, timezone)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, description, created_at, due_at, recurrence_type,
             recurrence_interval, recurrence_day_of_week, recurrence_day_of_month, timezone)
        )
        conn.commit()
        print(f"[MCP] task created with ID: {cursor.lastrowid}")
        return f"Task created with ID: {cursor.lastrowid}"


@mcp.tool()
async def list_tasks(show_completed: bool = False, limit: int = 20) -> str:
    """
    Lists tasks. By default shows only incomplete tasks.
    :param show_completed: If True, includes completed tasks.
    :param limit: Maximum number of tasks to return.
    """
    print(f"[MCP] list_tasks: show_completed={show_completed}, limit={limit}")
    with get_db() as conn:
        if show_completed:
            query = "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?"
        else:
            query = "SELECT * FROM tasks WHERE is_completed = 0 ORDER BY created_at DESC LIMIT ?"
        tasks = conn.execute(query, (limit,)).fetchall()
        if not tasks:
            return "No tasks found."
        lines = []
        for t in tasks:
            status = "(Done)" if t['is_completed'] else ""
            due = f" | Due: {t['due_at']}" if t['due_at'] else ""
            desc = f" - {t['description']}" if t['description'] else ""
            lines.append(f"[{t['id']}] {t['title']}{desc}{due} {status}")
        return "\n".join(lines)


@mcp.tool()
async def get_task(task_id: int) -> str:
    """
    Gets a single task by its ID.
    :param task_id: The ID of the task to retrieve.
    """
    print(f"[MCP] get_task: id={task_id}")
    with get_db() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            return f"Task with ID {task_id} not found."
        t = dict(task)
        status = "Completed" if t['is_completed'] else "Pending"
        due = t['due_at'] or "No due date"
        rec = t['recurrence_type']
        return (
            f"Task #{t['id']}: {t['title']}\n"
            f"Description: {t['description'] or 'None'}\n"
            f"Status: {status}\n"
            f"Due: {due}\n"
            f"Recurrence: {rec}\n"
            f"Created: {t['created_at']}"
        )


@mcp.tool()
async def update_task(
    task_id: int,
    title: str = None,
    description: str = None,
    due_at: str = None,
    recurrence_type: str = None,
    recurrence_interval: int = None,
    recurrence_day_of_week: int = None,
    recurrence_day_of_month: int = None,
    timezone: str = None
) -> str:
    """
    Updates an existing task. Only provided fields will be changed.
    :param task_id: The ID of the task to update.
    """
    print(f"[MCP] update_task: id={task_id}")
    fields = {
        'title': title, 'description': description, 'due_at': due_at,
        'recurrence_type': recurrence_type, 'recurrence_interval': recurrence_interval,
        'recurrence_day_of_week': recurrence_day_of_week,
        'recurrence_day_of_month': recurrence_day_of_month, 'timezone': timezone
    }
    update_fields = {k: v for k, v in fields.items() if v is not None}
    if not update_fields:
        return "No fields to update."

    set_clause = ", ".join([f"{key} = ?" for key in update_fields])
    values = list(update_fields.values()) + [task_id]

    with get_db() as conn:
        cursor = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", tuple(values))
        conn.commit()
        if cursor.rowcount == 0:
            return f"Task with ID {task_id} not found."
        return f"Task {task_id} updated."


@mcp.tool()
async def complete_task(task_id: int) -> str:
    """
    Marks a task as completed. If recurring, creates the next occurrence.
    :param task_id: The ID of the task to complete.
    """
    print(f"[MCP] complete_task: id={task_id}")
    with get_db() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            return f"Task with ID {task_id} not found."

        conn.execute("UPDATE tasks SET is_completed = 1 WHERE id = ?", (task_id,))
        conn.commit()

        if task['recurrence_type'] != 'none' and task['due_at']:
            current_due = datetime.datetime.strptime(task['due_at'], '%Y-%m-%d %H:%M:%S')
            interval = task['recurrence_interval']

            if task['recurrence_type'] == 'daily':
                new_due = current_due + datetime.timedelta(days=interval)
            elif task['recurrence_type'] == 'weekly':
                new_due = current_due + datetime.timedelta(weeks=interval)
            elif task['recurrence_type'] == 'monthly':
                month = current_due.month + interval
                year = current_due.year + (month - 1) // 12
                month = ((month - 1) % 12) + 1
                day = min(current_due.day, 28)
                new_due = current_due.replace(year=year, month=month, day=day)
            else:
                return f"Task {task_id} completed."

            await create_task(
                title=task['title'],
                description=task['description'],
                due_at=new_due.strftime('%Y-%m-%d %H:%M:%S'),
                recurrence_type=task['recurrence_type'],
                recurrence_interval=task['recurrence_interval'],
                recurrence_day_of_week=task['recurrence_day_of_week'],
                recurrence_day_of_month=task['recurrence_day_of_month'],
                timezone=task['timezone']
            )
            return f"Task {task_id} completed. Next recurring task created with due date {new_due.strftime('%Y-%m-%d %H:%M:%S')}."

        return f"Task {task_id} completed."


@mcp.tool()
async def delete_task(task_id: int) -> str:
    """
    Deletes a task.
    :param task_id: The ID of the task to delete.
    """
    print(f"[MCP] delete_task: id={task_id}")
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        if cursor.rowcount == 0:
            return f"Task with ID {task_id} not found."
        return f"Task {task_id} deleted."


@mcp.tool()
async def wake_on_lan() -> str:
    """Send a Wake-on-LAN magic packet to wake up the configured machine. No arguments needed."""
    mac = os.getenv("WAKE_MAC")
    if not mac:
        return "Error: WAKE_MAC not set in api/.env"
    try:
        mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", ""))
        if len(mac_bytes) != 6:
            return f"Error: invalid MAC address format: {mac}"
        magic = b"\xff" * 6 + mac_bytes * 16
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.connect(("255.255.255.255", 9))
            sock.send(magic)
        print(f"[MCP] wake_on_lan: magic packet sent to {mac}")
        return f"Wake-on-LAN magic packet sent to {mac}."
    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
