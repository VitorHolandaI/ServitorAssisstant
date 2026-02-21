from mcp.server.fastmcp import FastMCP
import openmeteo_requests
import requests_cache
from retry_requests import retry
import pandas as pd
import sqlite3
import datetime
from pathlib import Path
import asyncio

# --- Copied from tasks_server.py ---

# Define the project root and database path
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "tasks.db"

# Ensure the data directory exists
DB_PATH.parent.mkdir(exist_ok=True)

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def initialize_database():
    """Initializes the database and creates the tasks table if it doesn't exist."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
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
        conn.commit()

# Initialize the database on module load
initialize_database()


# --- Merged MCP object ---
mcp = FastMCP("GeneralAssistant")

# --- Copied from stream2.py ---

# Open-Meteo client with cache and retry
cache_session = requests_cache.CachedSession('.cache', expire_after=3600)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)


@mcp.tool()
async def default_response(description="CALL THIS TOOL WHEN NO TOOL EXACLT MATCHES WHAT THE USER WANTS") -> str:
    return "thres not tool to call, so you must now only repond the user questio"


@mcp.tool("add_numbers", description="Add two numbers and return the result.")
async def add_numbers(a: float, b: float):
    """Return the sum of two numbers."""
    return a + b


@mcp.tool("subtract_numbers", description="Subtract the second number from the first.")
async def subtract_numbers(a: float, b: float):
    """Return the result of subtracting b from a."""
    return a - b


@mcp.tool("multiply_numbers", description="Multiply two numbers together.")
async def multiply_numbers(a: float, b: float):
    """Return the product of two numbers."""
    return a * b


@mcp.tool("divide_numbers", description="Divide the first number by the second.")
async def divide_numbers(a: float, b: float):
    """Return the result of dividing a by b. Returns an error for division by zero."""
    if b == 0:
        return "Error: division by zero."
    return a / b


@mcp.tool()
async def get_forecast(latitude: float = -7.23071810, longitude: float = -35.88166640) -> str:
    """Get weather forecast for Campina Grande."""

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

        # Get the most recent hour's data
        now_idx = len(df) // 2  # approximate current hour
        # Try to find closest to current time
        now_utc = pd.Timestamp.now(tz="UTC")
        closest_idx = (df["date"] - now_utc).abs().argmin()

        row = df.iloc[closest_idx]
        temp = row["temperature_2m"]
        humidity = row["relative_humidity_2m"]
        rain = row["rain"]
        wind = row["wind_speed_10m"]

        return (
            f"Campina Grande, Paraiba - Current Weather:\n"
            f"Temperature: {temp:.1f}°C\n"
            f"Humidity: {humidity:.0f}%\n"
            f"Rain: {rain:.1f} mm\n"
            f"Wind: {wind:.1f} km/h"
        )
    except Exception as e:
        return f"Unable to fetch weather data: {e}"


# --- Copied from tasks_server.py ---
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
    :return: The ID of the created task.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        created_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("""
            INSERT INTO tasks (title, description, created_at, due_at, recurrence_type, recurrence_interval, recurrence_day_of_week, recurrence_day_of_month, timezone)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (title, description, created_at, due_at, recurrence_type, recurrence_interval, recurrence_day_of_week, recurrence_day_of_month, timezone))
        conn.commit()
        return f"Task created with ID: {cursor.lastrowid}"

@mcp.tool()
async def list_tasks(show_completed: bool = False, limit: int = 20) -> str:
    """
    Lists tasks. By default, it shows only incomplete tasks.
    :param show_completed: If True, includes completed tasks in the list.
    :param limit: The maximum number of tasks to return.
    :return: A list of tasks.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM tasks WHERE is_completed = 0"
        if show_completed:
            query = "SELECT * FROM tasks"
        query += f" ORDER BY created_at DESC LIMIT {limit}"
        cursor.execute(query)
        tasks = cursor.fetchall()
        if not tasks:
            return "No tasks found."
        return "\n".join([f"{task['id']}: {task['title']} {'(Completed)' if task['is_completed'] else ''}" for task in tasks])

@mcp.tool()
async def get_task(task_id: int) -> str:
    """
    Gets a single task by its ID.
    :param task_id: The ID of the task to retrieve.
    :return: The task details.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = cursor.fetchone()
        if task is None:
            return f"Task with ID {task_id} not found."
        return dict(task)

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
    Updates an existing task.
    :param task_id: The ID of the task to update.
    :param title: The new title.
    :param description: The new description.
    :param due_at: The new due date.
    :param recurrence_type: The new recurrence type.
    :param recurrence_interval: The new recurrence interval.
    :param recurrence_day_of_week: The new day of the week for recurrence.
    :param recurrence_day_of_month: The new day of the month for recurrence.
    :param timezone: The new timezone.
    :return: A confirmation message.
    """
    fields = {
        'title': title,
        'description': description,
        'due_at': due_at,
        'recurrence_type': recurrence_type,
        'recurrence_interval': recurrence_interval,
        'recurrence_day_of_week': recurrence_day_of_week,
        'recurrence_day_of_month': recurrence_day_of_month,
        'timezone': timezone
    }
    update_fields = {k: v for k, v in fields.items() if v is not None}

    if not update_fields:
        return "No fields to update."

    set_clause = ", ".join([f"{key} = ?" for key in update_fields.keys()])
    values = list(update_fields.values())
    values.append(task_id)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", tuple(values))
        conn.commit()
        if cursor.rowcount == 0:
            return f"Task with ID {task_id} not found."
        return f"Task {task_id} updated successfully."

@mcp.tool()
async def complete_task(task_id: int) -> str:
    """
    Marks a task as completed. If the task is recurring, it creates the next occurrence.
    :param task_id: The ID of the task to complete.
    :return: A confirmation message.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = cursor.fetchone()
        if task is None:
            return f"Task with ID {task_id} not found."

        cursor.execute("UPDATE tasks SET is_completed = 1 WHERE id = ?", (task_id,))
        conn.commit()

        # Handle recurrence
        if task['recurrence_type'] != 'none':
            # This is a simplified recurrence logic. A more robust solution would use a library.
            new_due_at = None
            if task['due_at']:
                current_due_at = datetime.datetime.strptime(task['due_at'], '%Y-%m-%d %H:%M:%S')
                if task['recurrence_type'] == 'daily':
                    new_due_at = current_due_at + datetime.timedelta(days=task['recurrence_interval'])
                elif task['recurrence_type'] == 'weekly':
                    new_due_at = current_due_at + datetime.timedelta(weeks=task['recurrence_interval'])
                # Add more complex recurrence logic for monthly, etc. if needed

            if new_due_at:
                await create_task(
                    title=task['title'],
                    description=task['description'],
                    due_at=new_due_at.strftime('%Y-%m-%d %H:%M:%S'),
                    recurrence_type=task['recurrence_type'],
                    recurrence_interval=task['recurrence_interval'],
                    recurrence_day_of_week=task['recurrence_day_of_week'],
                    recurrence_day_of_month=task['recurrence_day_of_month'],
                    timezone=task['timezone']
                )
                return f"Task {task_id} completed. Next recurring task created."

        return f"Task {task_id} completed."

@mcp.tool()
async def delete_task(task_id: int) -> str:
    """
    Deletes a task.
    :param task_id: The ID of the task to delete.
    :return: A confirmation message.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        if cursor.rowcount == 0:
            return f"Task with ID {task_id} not found."
        return f"Task {task_id} deleted."

@mcp.tool()
async def get_due_tasks() -> str:
    """
    Gets tasks that are due now or are overdue.
    :return: A list of due tasks.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("SELECT * FROM tasks WHERE is_completed = 0 AND due_at <= ?", (now,))
        tasks = cursor.fetchall()
        if not tasks:
            return "No due tasks."
        return "".join([f"{task['id']}: {task['title']} (Due: {task['due_at']})" for task in tasks])


# --- Main execution ---
if __name__ == "__main__":
    mcp.run(transport="streamable-http")
