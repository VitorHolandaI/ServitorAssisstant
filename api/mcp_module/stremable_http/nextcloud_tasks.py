import datetime
import os
import re
import ssl
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
import urllib3
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"
MAX_TASKS = 20
MAX_DESCRIPTION_CHARS = 120
MAX_TOOL_OUTPUT_CHARS = 4096


class NextcloudError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Calendar:
    name: str
    url: str
    components: frozenset[str]
    writable: bool
    timezone: str

    @property
    def slug(self) -> str:
        return unquote(urlparse(self.url).path.rstrip("/").rsplit("/", 1)[-1])


class NextcloudTasksClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        app_password: str,
        timezone: str = "America/Recife",
        task_calendar: str | None = None,
        reminder_calendar: str | None = None,
        tls_verify: bool | str | None = None,
        session: requests.Session | None = None,
    ):
        try:
            self.timezone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as error:
            raise NextcloudError(f"Invalid NC_TIMEZONE: {timezone}") from error

        self.base_url = base_url.rstrip("/")
        if urlparse(self.base_url).scheme.lower() != "https":
            raise NextcloudError("NC_URL must use HTTPS")
        self.username = username
        self.task_calendar = task_calendar
        self.reminder_calendar = reminder_calendar
        self.session = session or requests.Session()
        self.session.auth = (username, app_password)
        self.session.verify = tls_verify if tls_verify is not None else _default_ca_bundle()
        if self.session.verify is False:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.session.headers.update({"User-Agent": "ServitorAssistant/1.0"})

    @classmethod
    def from_env(cls):
        load_dotenv(PROJECT_ROOT / ".env")
        required = ("NC_URL", "NC_USER", "NC_APP_PASSWORD")
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise NextcloudError(
                f"Missing Nextcloud environment variables: {', '.join(missing)}"
            )
        tls_verify_value = os.getenv("NC_TLS_VERIFY", "true").strip().lower()
        if tls_verify_value in {"true", "1", "yes", "on"}:
            tls_verify = _default_ca_bundle()
        elif tls_verify_value in {"false", "0", "no", "off"}:
            tls_verify = False
        else:
            raise NextcloudError("NC_TLS_VERIFY must be true or false")
        return cls(
            base_url=os.environ["NC_URL"],
            username=os.environ["NC_USER"],
            app_password=os.environ["NC_APP_PASSWORD"],
            timezone=os.getenv("NC_TIMEZONE", "America/Recife"),
            task_calendar=os.getenv("NC_TASK_CALENDAR") or None,
            reminder_calendar=os.getenv("NC_REMINDER_CALENDAR") or None,
            tls_verify=tls_verify,
        )

    def _request(self, method: str, url: str, **kwargs) -> bytes:
        try:
            response = self.session.request(method, url, timeout=30, **kwargs)
            response.raise_for_status()
            return response.content
        except requests.exceptions.SSLError as error:
            raise NextcloudError(
                "Nextcloud TLS verification failed; configure NC_CA_BUNDLE or "
                "set NC_TLS_VERIFY=false for a trusted private network"
            ) from error
        except requests.HTTPError as error:
            status = error.response.status_code if error.response is not None else "unknown"
            raise NextcloudError(
                f"Nextcloud returned HTTP {status} for {method}",
                status_code=status if isinstance(status, int) else None,
            ) from error
        except requests.RequestException as error:
            raise NextcloudError(f"Could not reach Nextcloud: {type(error).__name__}") from error

    def close(self) -> None:
        self.session.close()

    def discover_calendars(self) -> list[Calendar]:
        body = """<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:displayname/>
    <d:resourcetype/>
    <d:current-user-privilege-set/>
    <c:supported-calendar-component-set/>
    <c:calendar-timezone/>
  </d:prop>
</d:propfind>"""
        user = quote(self.username, safe="")
        home_url = f"{self.base_url}/remote.php/dav/calendars/{user}/"
        data = self._request(
            "PROPFIND",
            home_url,
            data=body.encode(),
            headers={"Depth": "1", "Content-Type": "application/xml"},
        )
        try:
            root = ET.fromstring(data)
        except ET.ParseError as error:
            raise NextcloudError("Nextcloud returned invalid calendar XML") from error

        calendars = []
        for response in root.findall(f"{{{DAV}}}response"):
            resource_type = response.find(f".//{{{DAV}}}resourcetype")
            if resource_type is None or resource_type.find(f"{{{CALDAV}}}calendar") is None:
                continue

            href = response.findtext(f"{{{DAV}}}href", default="")
            name = response.findtext(f".//{{{DAV}}}displayname", default=href)
            components = frozenset(
                element.get("name", "").upper()
                for element in response.findall(
                    f".//{{{CALDAV}}}supported-calendar-component-set/"
                    f"{{{CALDAV}}}comp"
                )
                if element.get("name")
            )
            privileges = response.findall(
                f".//{{{DAV}}}current-user-privilege-set/"
                f"{{{DAV}}}privilege/*"
            )
            writable = not privileges or any(
                element.tag in {f"{{{DAV}}}write", f"{{{DAV}}}all"}
                for element in privileges
            )
            timezone_data = response.findtext(
                f".//{{{CALDAV}}}calendar-timezone", default=""
            )
            calendars.append(
                Calendar(
                    name=name,
                    url=urljoin(f"{self.base_url}/", href),
                    components=components,
                    writable=writable,
                    timezone=_extract_calendar_timezone(timezone_data) or "UTC",
                )
            )
        return calendars

    def _select_calendar(
        self,
        calendars: list[Calendar],
        component: str,
        configured_name: str | None,
    ) -> Calendar:
        compatible = [
            calendar
            for calendar in calendars
            if calendar.writable
            and (not calendar.components or component in calendar.components)
        ]
        if configured_name:
            wanted = configured_name.casefold()
            matches = [
                calendar
                for calendar in compatible
                if wanted in {calendar.name.casefold(), calendar.slug.casefold()}
            ]
            if len(matches) == 1:
                return matches[0]
            if not matches:
                raise NextcloudError(
                    f"Configured calendar '{configured_name}' does not support {component}"
                )
            raise NextcloudError(f"Calendar selector '{configured_name}' is ambiguous")

        if len(compatible) == 1:
            return compatible[0]
        if not compatible:
            raise NextcloudError(f"No writable Nextcloud calendar supports {component}")

        preferred_names = (
            ("tasks", "personal")
            if component == "VTODO"
            else ("personal", "calendar")
        )
        for preferred_name in preferred_names:
            preferred = [
                calendar
                for calendar in compatible
                if preferred_name
                in {calendar.name.casefold(), calendar.slug.casefold()}
            ]
            if len(preferred) == 1:
                return preferred[0]

        names = ", ".join(calendar.name for calendar in compatible[:8])
        variable = "NC_TASK_CALENDAR" if component == "VTODO" else "NC_REMINDER_CALENDAR"
        raise NextcloudError(f"Set {variable}; available calendars: {names}")

    def _task_calendars(
        self,
        calendars: list[Calendar],
        selector: str | None = None,
        writable: bool = False,
    ) -> list[Calendar]:
        compatible = [
            calendar
            for calendar in calendars
            if (not calendar.components or "VTODO" in calendar.components)
            and (not writable or calendar.writable)
        ]
        if not selector:
            return compatible
        wanted = selector.strip().casefold()
        matches = [
            calendar
            for calendar in compatible
            if wanted in {calendar.name.casefold(), calendar.slug.casefold()}
        ]
        if not matches:
            names = ", ".join(calendar.name for calendar in compatible[:10])
            raise NextcloudError(
                f"Nextcloud task list not found: {selector}. Available lists: {names}"
            )
        if len(matches) > 1:
            raise NextcloudError(f"Nextcloud task list is ambiguous: {selector}")
        return matches

    def _find_task(
        self,
        identifier: str,
        calendar: str | None = None,
        writable: bool = False,
    ) -> tuple[dict, Calendar]:
        identifier = _single_line(identifier).strip()
        if not identifier:
            raise NextcloudError("Task title or UID cannot be empty")
        calendars = self.discover_calendars()
        task_calendars = self._task_calendars(calendars, calendar, writable=writable)
        tasks = []
        calendars_by_url = {item.url: item for item in task_calendars}
        for task_calendar in task_calendars:
            tasks.extend(self._calendar_query(task_calendar, show_completed=True))
        matches = _match_tasks(tasks, identifier)
        if not matches:
            raise NextcloudError(
                f"No Nextcloud task matches title or UID: {identifier}"
            )
        if len(matches) > 1:
            choices = ", ".join(
                f"[{item.get('UID', 'unknown')[:8]}] {item.get('SUMMARY', 'Untitled')}"
                for item in matches[:5]
            )
            raise NextcloudError(f"Task match is ambiguous; use a short UID: {choices}")
        selected = matches[0]
        return selected, calendars_by_url[selected["calendar_url"]]

    def _calendar_query(self, calendar: Calendar, show_completed: bool) -> list[dict]:
        completed_filter = ""
        if not show_completed:
            completed_filter = """
        <c:prop-filter name="COMPLETED">
          <c:is-not-defined/>
        </c:prop-filter>"""
        body = f"""<?xml version="1.0"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag/>
    <c:calendar-data>
      <c:comp name="VCALENDAR">
        <c:prop name="VERSION"/>
        <c:comp name="VTODO">
          <c:prop name="UID"/>
          <c:prop name="SUMMARY"/>
          <c:prop name="DESCRIPTION"/>
          <c:prop name="DTSTART"/>
          <c:prop name="DUE"/>
          <c:prop name="STATUS"/>
          <c:prop name="COMPLETED"/>
          <c:prop name="PERCENT-COMPLETE"/>
          <c:prop name="CREATED"/>
          <c:prop name="LAST-MODIFIED"/>
          <c:prop name="CATEGORIES"/>
          <c:prop name="PRIORITY"/>
        </c:comp>
      </c:comp>
    </c:calendar-data>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VTODO">{completed_filter}
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""
        data = self._request(
            "REPORT",
            calendar.url,
            data=body.encode(),
            headers={"Depth": "1", "Content-Type": "application/xml"},
        )
        try:
            root = ET.fromstring(data)
        except ET.ParseError as error:
            raise NextcloudError("Nextcloud returned invalid task XML") from error

        tasks = []
        for response in root.findall(f"{{{DAV}}}response"):
            href = response.findtext(f"{{{DAV}}}href", default="")
            etag = response.findtext(f".//{{{DAV}}}getetag")
            calendar_data = response.findtext(f".//{{{CALDAV}}}calendar-data")
            if not calendar_data:
                continue
            alarms = _alarm_minutes_by_uid(calendar_data, "VTODO")
            for task in _parse_vtodos(calendar_data):
                if task.get("STATUS") == "CANCELLED":
                    continue
                task["calendar"] = calendar.name
                task["calendar_url"] = calendar.url
                task["href"] = href
                task["etag"] = etag
                task["due_datetime"] = _parse_ical_datetime(
                    task.get("DUE"), task.get("DUE_parameters"), self.timezone
                )
                task["reminder_minutes_before"] = alarms.get(task.get("UID"), 0)
                tasks.append(task)
        return tasks

    def _calendar_event_query(
        self,
        calendar: Calendar,
        range_start: datetime.datetime,
        range_end: datetime.datetime,
    ) -> list[dict]:
        start_value = _format_utc(range_start)
        end_value = _format_utc(range_end)
        body = f"""<?xml version="1.0"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag/>
    <c:calendar-data>
      <c:expand start="{start_value}" end="{end_value}"/>
    </c:calendar-data>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{start_value}" end="{end_value}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""
        data = self._request(
            "REPORT",
            calendar.url,
            data=body.encode(),
            headers={"Depth": "1", "Content-Type": "application/xml"},
        )
        try:
            root = ET.fromstring(data)
        except ET.ParseError as error:
            raise NextcloudError("Nextcloud returned invalid event XML") from error

        events = []
        calendar_timezone = ZoneInfo(calendar.timezone)
        for response in root.findall(f"{{{DAV}}}response"):
            href = response.findtext(f"{{{DAV}}}href", default="")
            calendar_data = response.findtext(f".//{{{CALDAV}}}calendar-data")
            if not calendar_data:
                continue
            alarms = _alarm_minutes_by_uid(calendar_data, "VEVENT")
            for event in _parse_ical_components(calendar_data, "VEVENT"):
                if event.get("STATUS") == "CANCELLED":
                    continue
                event["calendar"] = calendar.name
                event["href"] = href
                event["reminder_minutes_before"] = alarms.get(event.get("UID"), 0)
                event["start_datetime"] = _parse_ical_datetime(
                    event.get("DTSTART"),
                    event.get("DTSTART_parameters"),
                    calendar_timezone,
                )
                event["end_datetime"] = _parse_ical_datetime(
                    event.get("DTEND"),
                    event.get("DTEND_parameters"),
                    calendar_timezone,
                )
                event["all_day"] = bool(
                    event.get("DTSTART") and "T" not in event["DTSTART"]
                )
                if event["start_datetime"] is not None:
                    events.append(event)
        return events

    def list_tasks(
        self,
        show_completed: bool = False,
        limit: int = 10,
        calendar: str | None = None,
    ) -> str:
        limit = max(1, min(int(limit), MAX_TASKS))
        calendars = self.discover_calendars()
        tasks = []
        for task_calendar in self._task_calendars(calendars, calendar):
            tasks.extend(self._calendar_query(task_calendar, show_completed))

        tasks.sort(
            key=lambda task: (
                task["due_datetime"] is None,
                task["due_datetime"] or datetime.datetime.max.replace(tzinfo=datetime.UTC),
                task.get("SUMMARY", "").casefold(),
            )
        )
        if not tasks:
            return "No Nextcloud tasks found."

        selected = tasks[:limit]
        lines = [f"Showing {len(selected)} of {len(tasks)} Nextcloud tasks:"]
        shown = 0
        for task in selected:
            uid = task.get("UID", "unknown")[:8]
            title = _single_line(task.get("SUMMARY") or "Untitled task")[:200]
            due = _display_datetime(task["due_datetime"], self.timezone)
            status = "done" if task.get("COMPLETED") or task.get("STATUS") == "COMPLETED" else "pending"
            line = f"[{uid}] {title} | {status}"
            if due:
                line += f" | due {due}"
            line += f" | list {task['calendar']}"
            description = _single_line(task.get("DESCRIPTION", ""))
            if len(description) > MAX_DESCRIPTION_CHARS:
                description = description[: MAX_DESCRIPTION_CHARS - 3].rstrip() + "..."
            if description:
                line += f"\n  {description}"
            if len("\n".join(lines + [line])) > MAX_TOOL_OUTPUT_CHARS:
                break
            lines.append(line)
            shown += 1

        omitted = len(tasks) - shown
        if omitted:
            lines.append(f"{omitted} additional tasks omitted to protect agent context.")
        return "\n".join(lines)[:MAX_TOOL_OUTPUT_CHARS]

    def list_events(self, date: str | None = None, limit: int = 10) -> str:
        limit = max(1, min(int(limit), MAX_TASKS))
        now = datetime.datetime.now(self.timezone)
        if date is None:
            query_date = now.date()
        else:
            try:
                query_date = datetime.datetime.strptime(date, "%Y-%m-%d").date()
            except (TypeError, ValueError) as error:
                raise NextcloudError("date must use YYYY-MM-DD format") from error

        day_start = datetime.datetime.combine(
            query_date, datetime.time.min, tzinfo=self.timezone
        )
        day_end = day_start + datetime.timedelta(days=1)
        calendars = self.discover_calendars()
        events = []
        for calendar in calendars:
            if calendar.components and "VEVENT" not in calendar.components:
                continue
            events.extend(self._calendar_event_query(calendar, day_start, day_end))

        unique_events = {}
        for event in events:
            key = (
                event["calendar"],
                event.get("UID"),
                event.get("RECURRENCE-ID"),
                event.get("DTSTART"),
            )
            unique_events[key] = event
        events = list(unique_events.values())
        events.sort(
            key=lambda event: (
                not event["all_day"],
                event["start_datetime"],
                event.get("SUMMARY", "").casefold(),
            )
        )

        is_today = query_date == now.date()
        current_time = now.strftime("%Y-%m-%d %H:%M:%S %Z")
        if not events:
            return (
                f"No Nextcloud events found for {query_date.isoformat()}. "
                f"Current time: {current_time}."
            )

        selected = events[:limit]
        header = f"Calendar for {query_date.isoformat()}"
        if is_today:
            header += f" (current time {now.strftime('%H:%M:%S %Z')})"
        lines = [f"{header}: showing {len(selected)} of {len(events)} events."]
        shown = 0
        now_utc = now.astimezone(datetime.UTC)
        for event in selected:
            start = event["start_datetime"]
            end = event["end_datetime"]
            if event["all_day"]:
                time_label = "all day"
                state = "today" if is_today else "scheduled"
            else:
                local_start = start.astimezone(self.timezone)
                local_end = end.astimezone(self.timezone) if end else None
                time_label = local_start.strftime("%H:%M")
                if local_end:
                    time_label += f"-{local_end.strftime('%H:%M')}"
                effective_end = end or start + datetime.timedelta(minutes=1)
                if not is_today:
                    state = "scheduled"
                elif start <= now_utc < effective_end:
                    state = "ongoing"
                elif start > now_utc:
                    state = "upcoming"
                else:
                    state = "ended"

            title = _single_line(event.get("SUMMARY") or "Untitled event")[:200]
            line = f"[{state}] {time_label} | {title} | {event['calendar']}"
            location = _single_line(event.get("LOCATION", ""))
            if location:
                line += f" | {location[:100]}"
            description = _single_line(event.get("DESCRIPTION", ""))
            if len(description) > MAX_DESCRIPTION_CHARS:
                description = description[: MAX_DESCRIPTION_CHARS - 3].rstrip() + "..."
            if description:
                line += f"\n  {description}"
            if len("\n".join(lines + [line])) > MAX_TOOL_OUTPUT_CHARS:
                break
            lines.append(line)
            shown += 1

        omitted = len(events) - shown
        if omitted:
            lines.append(f"{omitted} additional events omitted to protect agent context.")
        return "\n".join(lines)[:MAX_TOOL_OUTPUT_CHARS]

    def get_task(self, task: str, calendar: str | None = None) -> str:
        selected, _ = self._find_task(task, calendar)
        uid = selected.get("UID", "unknown")
        status = selected.get("STATUS", "NEEDS-ACTION")
        due = _display_datetime(selected.get("due_datetime"), self.timezone) or "none"
        description = _single_line(selected.get("DESCRIPTION", "")) or "none"
        lines = [
            f"Nextcloud task [{uid}]",
            f"Title: {selected.get('SUMMARY', 'Untitled task')}",
            f"List: {selected['calendar']}",
            f"Status: {status}",
            f"Due: {due}",
            f"Description: {description}",
        ]
        for label, property_name in (
            ("Categories", "CATEGORIES"),
            ("Priority", "PRIORITY"),
            ("Created", "CREATED"),
            ("Last modified", "LAST-MODIFIED"),
        ):
            if selected.get(property_name):
                lines.append(f"{label}: {_single_line(selected[property_name])}")
        return "\n".join(lines)[:MAX_TOOL_OUTPUT_CHARS]

    def complete_task(
        self,
        task: str,
        calendar: str | None = None,
    ) -> str:
        selected, _ = self._find_task(task, calendar, writable=True)
        uid = selected.get("UID")
        if not uid:
            raise NextcloudError("Matched Nextcloud task has no UID")
        if selected.get("COMPLETED") or selected.get("STATUS") == "COMPLETED":
            return f"Nextcloud task already completed [{uid[:8]}]: {selected.get('SUMMARY', task)}"
        if not selected.get("etag"):
            raise NextcloudError("Nextcloud did not return an ETag for the task")

        resource_url = urljoin(f"{self.base_url}/", selected["href"])
        raw_ical = self._request("GET", resource_url).decode("utf-8")
        now = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
        updated_ical = _mark_vtodo_completed(raw_ical, uid, now)
        try:
            self._request(
                "PUT",
                resource_url,
                data=updated_ical.encode("utf-8"),
                headers={
                    "Content-Type": "text/calendar; charset=utf-8",
                    "If-Match": selected["etag"],
                },
            )
        except NextcloudError as error:
            if error.status_code == 412:
                raise NextcloudError(
                    "Nextcloud task changed concurrently; list it again and retry"
                ) from error
            raise

        return f"Nextcloud task completed [{uid[:8]}]: {selected.get('SUMMARY', task)}"

    def update_task(
        self,
        task: str,
        title: str | None = None,
        description: str | None = None,
        due_at: str | None = None,
        status: str | None = None,
        calendar: str | None = None,
    ) -> str:
        if all(value is None for value in (title, description, due_at, status)):
            raise NextcloudError("Provide at least one task field to update")
        selected, _ = self._find_task(task, calendar, writable=True)
        uid = selected.get("UID")
        if not uid or not selected.get("etag"):
            raise NextcloudError("Matched Nextcloud task has no UID or ETag")

        replacements = {}
        if title is not None:
            title = _single_line(title).strip()
            if not title:
                raise NextcloudError("Task title cannot be empty")
            replacements["SUMMARY"] = f"SUMMARY:{_escape_ical_text(title)}"
        if description is not None:
            replacements["DESCRIPTION"] = (
                f"DESCRIPTION:{_escape_ical_text(description)}" if description else None
            )
        if due_at is not None:
            replacements["DUE"] = (
                f"DUE:{_format_utc(_parse_user_datetime(due_at, self.timezone))}"
                if due_at.strip()
                else None
            )
        normalized_status = None
        if status is not None:
            normalized_status = status.strip().upper().replace("_", "-").replace(" ", "-")
            aliases = {
                "PENDING": "NEEDS-ACTION",
                "OPEN": "NEEDS-ACTION",
                "REOPEN": "NEEDS-ACTION",
                "DONE": "COMPLETED",
                "COMPLETE": "COMPLETED",
            }
            normalized_status = aliases.get(normalized_status, normalized_status)
            if normalized_status not in {
                "NEEDS-ACTION",
                "IN-PROCESS",
                "COMPLETED",
                "CANCELLED",
            }:
                raise NextcloudError(
                    "status must be needs-action, in-process, completed, or cancelled"
                )
            replacements.update(
                {
                    "STATUS": f"STATUS:{normalized_status}",
                    "COMPLETED": None,
                    "PERCENT-COMPLETE": (
                        "PERCENT-COMPLETE:50"
                        if normalized_status == "IN-PROCESS"
                        else "PERCENT-COMPLETE:0"
                    ),
                }
            )

        resource_url = urljoin(f"{self.base_url}/", selected["href"])
        raw_ical = self._request("GET", resource_url).decode("utf-8")
        now = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
        if normalized_status == "COMPLETED":
            replacements.update(
                {
                    "STATUS": "STATUS:COMPLETED",
                    "PERCENT-COMPLETE": "PERCENT-COMPLETE:100",
                    "COMPLETED": f"COMPLETED:{_format_utc(now)}",
                }
            )
        updated_ical = _rewrite_vtodo_properties(raw_ical, uid, replacements, now)
        self._conditional_task_request(
            "PUT",
            resource_url,
            selected["etag"],
            data=updated_ical.encode("utf-8"),
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )
        return f"Nextcloud task updated [{uid[:8]}]: {title or selected.get('SUMMARY', task)}"

    def delete_task(self, task: str, calendar: str | None = None) -> str:
        selected, _ = self._find_task(task, calendar, writable=True)
        uid = selected.get("UID")
        if not uid or not selected.get("etag"):
            raise NextcloudError("Matched Nextcloud task has no UID or ETag")
        resource_url = urljoin(f"{self.base_url}/", selected["href"])
        self._conditional_task_request("DELETE", resource_url, selected["etag"])
        return f"Nextcloud task deleted [{uid[:8]}]: {selected.get('SUMMARY', task)}"

    def move_task(
        self,
        task: str,
        destination_calendar: str,
        calendar: str | None = None,
    ) -> str:
        selected, source_calendar = self._find_task(task, calendar, writable=True)
        calendars = self.discover_calendars()
        destination = self._task_calendars(
            calendars, destination_calendar, writable=True
        )[0]
        uid = selected.get("UID")
        if not uid or not selected.get("etag"):
            raise NextcloudError("Matched Nextcloud task has no UID or ETag")
        if source_calendar.url == destination.url:
            return f"Nextcloud task already in list {destination.name} [{uid[:8]}]"

        resource_url = urljoin(f"{self.base_url}/", selected["href"])
        resource_name = urlparse(resource_url).path.rsplit("/", 1)[-1]
        destination_url = urljoin(destination.url.rstrip("/") + "/", resource_name)
        self._conditional_task_request(
            "MOVE",
            resource_url,
            selected["etag"],
            headers={"Destination": destination_url, "Overwrite": "F"},
        )
        return (
            f"Nextcloud task moved [{uid[:8]}]: {selected.get('SUMMARY', task)} "
            f"from {source_calendar.name} to {destination.name}"
        )

    def _conditional_task_request(
        self,
        method: str,
        url: str,
        etag: str,
        **kwargs,
    ) -> bytes:
        headers = dict(kwargs.pop("headers", {}))
        headers["If-Match"] = etag
        try:
            return self._request(method, url, headers=headers, **kwargs)
        except NextcloudError as error:
            if error.status_code == 412:
                raise NextcloudError(
                    "Nextcloud task changed concurrently; list it again and retry"
                ) from error
            raise

    def _resource_exists(self, url: str) -> bool:
        try:
            self._request("GET", url)
            return True
        except NextcloudError as error:
            if error.status_code == 404:
                return False
            raise

    def _put_with_probe(self, url: str, data: str, headers: dict[str, str]) -> None:
        try:
            self._request("PUT", url, data=data.encode(), headers=headers)
        except NextcloudError:
            if self._resource_exists(url):
                return
            raise

    def _rollback_resources(self, *urls: str) -> None:
        for url in urls:
            try:
                self._request("DELETE", url)
            except NextcloudError:
                pass

    def create_task(
        self,
        title: str,
        due_at: str,
        description: str | None = None,
        reminder_minutes_before: int = 0,
        calendar: str | None = None,
    ) -> str:
        title = _single_line(title).strip()
        if not title:
            raise NextcloudError("Task title cannot be empty")
        if len(title) > 500:
            raise NextcloudError("Task title cannot exceed 500 characters")

        due = _parse_user_datetime(due_at, self.timezone)
        try:
            reminder_minutes_before = int(reminder_minutes_before)
        except (TypeError, ValueError) as error:
            raise NextcloudError("reminder_minutes_before must be an integer") from error
        if not 0 <= reminder_minutes_before <= 525600:
            raise NextcloudError("reminder_minutes_before must be between 0 and 525600")
        reminder_at = due - datetime.timedelta(minutes=reminder_minutes_before)
        if reminder_at <= datetime.datetime.now(datetime.UTC):
            raise NextcloudError("Reminder time must be in the future")

        calendars = self.discover_calendars()
        task_calendar = self._select_calendar(
            calendars, "VTODO", calendar or self.task_calendar
        )
        if (
            not self.reminder_calendar
            and (not task_calendar.components or "VEVENT" in task_calendar.components)
        ):
            reminder_calendar = task_calendar
        else:
            reminder_calendar = self._select_calendar(
                calendars, "VEVENT", self.reminder_calendar
            )

        for existing in self._calendar_query(task_calendar, show_completed=False):
            existing_due = existing.get("due_datetime")
            if (
                (existing.get("SUMMARY") or "").strip().casefold() == title.casefold()
                and existing_due
                and existing_due.replace(second=0, microsecond=0)
                == due.replace(second=0, microsecond=0)
            ):
                uid = existing.get("UID", "unknown")[:8]
                return f"Task already exists in Nextcloud [{uid}]: {title}"

        duplicate_key = "|".join(
            (
                self.username,
                task_calendar.url,
                title.casefold(),
                due.replace(second=0, microsecond=0).isoformat(),
            )
        )
        task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, duplicate_key))
        task_uid = f"{task_id}@servitor"
        event_uid = f"reminder-{task_id}@servitor"
        task_url = urljoin(task_calendar.url.rstrip("/") + "/", quote(f"{task_id}.ics"))
        event_url = urljoin(
            reminder_calendar.url.rstrip("/") + "/",
            quote(f"reminder-{task_id}.ics"),
        )
        now = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
        trigger = _alarm_trigger(reminder_minutes_before)
        task_ics = _build_task_ics(
            task_uid,
            title,
            description or "",
            due,
            now,
            trigger,
        )
        event_ics = _build_reminder_event_ics(
            event_uid,
            task_uid,
            title,
            description or "",
            due,
            now,
            trigger,
            reminder_calendar.timezone,
        )
        put_headers = {
            "Content-Type": "text/calendar; charset=utf-8",
            "If-None-Match": "*",
        }

        try:
            self._put_with_probe(task_url, task_ics, put_headers)
            self._put_with_probe(event_url, event_ics, put_headers)
        except NextcloudError as create_error:
            self._rollback_resources(event_url, task_url)
            raise NextcloudError(
                "Nextcloud task/reminder creation failed; rollback was attempted"
            ) from create_error

        due_display = _display_datetime(due, self.timezone)
        reminder_display = _display_datetime(reminder_at, self.timezone)
        return (
            f"Nextcloud task created [{task_id[:8]}]: {title}. "
            f"Due: {due_display}. Reminder: {reminder_display}."
        )

    def set_task_reminder(
        self,
        task: str,
        reminder_minutes_before: int,
        calendar: str | None = None,
    ) -> str:
        try:
            reminder_minutes_before = int(reminder_minutes_before)
        except (TypeError, ValueError) as error:
            raise NextcloudError("reminder_minutes_before must be an integer") from error
        if not 0 <= reminder_minutes_before <= 525600:
            raise NextcloudError("reminder_minutes_before must be between 0 and 525600")

        selected, task_calendar = self._find_task(task, calendar, writable=True)
        uid = selected.get("UID")
        due = selected.get("due_datetime")
        etag = selected.get("etag")
        if not uid or not etag:
            raise NextcloudError("Matched Nextcloud task has no UID or ETag")
        if due is None:
            raise NextcloudError("A due date is required before adding a reminder")
        reminder_at = due - datetime.timedelta(minutes=reminder_minutes_before)
        if reminder_at <= datetime.datetime.now(datetime.UTC):
            raise NextcloudError("Reminder time must be in the future")

        title = selected.get("SUMMARY") or "Untitled task"
        description = selected.get("DESCRIPTION") or ""
        now = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
        trigger = _alarm_trigger(reminder_minutes_before)
        resource_url = urljoin(f"{self.base_url}/", selected["href"])
        raw_ical = self._request("GET", resource_url).decode("utf-8")
        updated_task = _set_vtodo_alarm(raw_ical, uid, title, trigger, now)

        calendars = self.discover_calendars()
        if (
            not self.reminder_calendar
            and (not task_calendar.components or "VEVENT" in task_calendar.components)
        ):
            reminder_calendar = task_calendar
        else:
            reminder_calendar = self._select_calendar(
                calendars, "VEVENT", self.reminder_calendar
            )
        reminder_id = _reminder_id_for_task(self.username, uid)
        event_uid = f"reminder-{reminder_id}@servitor"
        event_url = urljoin(
            reminder_calendar.url.rstrip("/") + "/",
            quote(f"reminder-{reminder_id}.ics"),
        )
        event_ics = _build_reminder_event_ics(
            event_uid,
            uid,
            title,
            description,
            due,
            now,
            trigger,
            reminder_calendar.timezone,
        )

        self._request(
            "PUT",
            event_url,
            data=event_ics.encode("utf-8"),
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )
        self._conditional_task_request(
            "PUT",
            resource_url,
            etag,
            data=updated_task.encode("utf-8"),
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )
        return (
            f"Nextcloud task reminder set [{uid[:8]}]: {title}. "
            f"Reminder: {_display_datetime(reminder_at, self.timezone)}."
        )

    def snapshot_agenda(
        self,
        start_date: str | None = None,
        days: int = 7,
        include_overdue_tasks: bool = True,
        include_undated_tasks: bool = False,
        task_lists: str | None = None,
        event_calendars: str | None = None,
        all_tasks: bool = False,
    ) -> dict:
        """Structured 7-day snapshot of events + tasks for an appliance.

        Events cover the half-open local range [start_date 00:00, +days 00:00).
        Tasks default to the same window (due-in-range + overdue + optional
        undated); pass all_tasks=True to include every incomplete task regardless
        of due date (full per-list task view, like the Nextcloud Tasks app).
        Returns a JSON-serializable dict; see docs/mcp/nextcloud-appliance-sync.md.
        Example: client.snapshot_agenda(days=7, all_tasks=True).
        """
        days = _validate_snapshot_days(days)
        start_local, end_local = self._snapshot_range(start_date, days)
        start_utc = start_local.astimezone(datetime.UTC)
        end_utc = end_local.astimezone(datetime.UTC)
        calendars = self.discover_calendars()
        errors: list[dict] = []
        events = self._collect_snapshot_events(
            calendars, start_utc, end_utc, event_calendars, errors
        )
        tasks = self._collect_snapshot_tasks(
            calendars, start_utc, end_utc,
            include_overdue_tasks, include_undated_tasks, task_lists, errors, all_tasks,
        )
        generated_at = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
        return self._build_snapshot(
            generated_at, start_local, end_local, days, events, tasks, errors
        )

    def _snapshot_range(
        self, start_date: str | None, days: int
    ) -> tuple[datetime.datetime, datetime.datetime]:
        if start_date is None:
            query_date = datetime.datetime.now(self.timezone).date()
        else:
            try:
                query_date = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
            except (TypeError, ValueError) as error:
                raise NextcloudError(
                    f"start_date must use YYYY-MM-DD format, got {start_date!r}"
                ) from error
        start_local = datetime.datetime.combine(
            query_date, datetime.time.min, tzinfo=self.timezone
        )
        return start_local, start_local + datetime.timedelta(days=days)

    def _collect_snapshot_events(
        self,
        calendars: list[Calendar],
        start_utc: datetime.datetime,
        end_utc: datetime.datetime,
        selector: str | None,
        errors: list[dict],
    ) -> list[dict]:
        wanted = _selector_set(selector)
        unique: dict[str, dict] = {}
        for calendar in calendars:
            if calendar.components and "VEVENT" not in calendar.components:
                continue
            if wanted and not _calendar_matches(calendar, wanted):
                continue
            try:
                found = self._calendar_event_query(calendar, start_utc, end_utc)
            except NextcloudError as error:
                errors.append({"calendar": calendar.name, "error": str(error)})
                continue
            for event in found:
                if event.get("X-SERVITOR-TASK-UID"):
                    continue  # linked reminder event; the task itself carries it
                serialized = _serialize_snapshot_event(event)
                unique[serialized["key"]] = serialized
        return sorted(
            unique.values(), key=lambda item: (item["start"], item["title"].casefold())
        )

    def _collect_snapshot_tasks(
        self,
        calendars: list[Calendar],
        start_utc: datetime.datetime,
        end_utc: datetime.datetime,
        include_overdue: bool,
        include_undated: bool,
        selector: str | None,
        errors: list[dict],
        all_tasks: bool = False,
    ) -> list[dict]:
        try:
            task_calendars = self._task_calendars(calendars, selector)
        except NextcloudError as error:
            errors.append({"calendar": selector or "tasks", "error": str(error)})
            return []
        tasks: list[dict] = []
        for calendar in task_calendars:
            try:
                found = self._calendar_query(calendar, show_completed=False)
            except NextcloudError as error:
                errors.append({"calendar": calendar.name, "error": str(error)})
                continue
            for task in found:
                serialized = _classify_snapshot_task(
                    task, start_utc, end_utc, include_overdue, include_undated, all_tasks
                )
                if serialized is not None:
                    tasks.append(serialized)
        tasks.sort(
            key=lambda item: (item["due"] is None, item["due"] or "", item["title"].casefold())
        )
        return tasks

    def _build_snapshot(
        self,
        generated_at: datetime.datetime,
        start_local: datetime.datetime,
        end_local: datetime.datetime,
        days: int,
        events: list[dict],
        tasks: list[dict],
        errors: list[dict],
    ) -> dict:
        return {
            "schema_version": 1,
            "snapshot_id": f"{_iso_utc(generated_at)}/{uuid.uuid4()}",
            "generated_at": _iso_utc(generated_at),
            "timezone": str(self.timezone),
            "range": {
                "start_local": start_local.isoformat(),
                "end_local_exclusive": end_local.isoformat(),
                "days": days,
            },
            "events": events,
            "tasks": tasks,
            "counts": {
                "events": len(events),
                "tasks": len(tasks),
                "overdue_tasks": sum(1 for task in tasks if task["overdue"]),
                "undated_tasks": sum(1 for task in tasks if task["due"] is None),
            },
            "complete": not errors,
            "errors": errors,
        }


def _validate_snapshot_days(days: int) -> int:
    try:
        days = int(days)
    except (TypeError, ValueError) as error:
        raise NextcloudError(f"days must be an integer, got {days!r}") from error
    if not 1 <= days <= 31:
        raise NextcloudError(f"days must be between 1 and 31, got {days}")
    return days


def _classify_snapshot_task(
    task: dict,
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
    include_overdue: bool,
    include_undated: bool,
    all_tasks: bool = False,
) -> dict | None:
    if task.get("COMPLETED") or task.get("STATUS") == "COMPLETED":
        return None
    due = task.get("due_datetime")
    if all_tasks:
        # Full per-list view: every incomplete task, no date window.
        return _serialize_snapshot_task(task, due is not None and due < start_utc)
    if due is None:
        if not include_undated:
            return None
        overdue = False
    elif due < start_utc:
        if not include_overdue:
            return None
        overdue = True
    elif due >= end_utc:
        return None
    else:
        overdue = False
    return _serialize_snapshot_task(task, overdue)


def _serialize_snapshot_event(event: dict) -> dict:
    start = _iso_utc(event["start_datetime"])
    uid = event.get("UID", "")
    recurrence_id = event.get("RECURRENCE-ID")
    return {
        "key": f"{event['calendar']}/{uid}/{recurrence_id or start}",
        "uid": uid,
        "recurrence_id": recurrence_id,
        "calendar": event["calendar"],
        "title": _single_line(event.get("SUMMARY") or "Untitled event"),
        "description": _single_line(event.get("DESCRIPTION", "")),
        "location": _single_line(event.get("LOCATION", "")),
        "start": start,
        "end": _iso_utc(event["end_datetime"]) if event.get("end_datetime") else None,
        "all_day": event.get("all_day", False),
        "status": event.get("STATUS", "CONFIRMED"),
        "reminder_minutes_before": event.get("reminder_minutes_before", 0),
        "last_modified": _iso_property_utc(event.get("LAST-MODIFIED")),
    }


def _serialize_snapshot_task(task: dict, overdue: bool) -> dict:
    due = task.get("due_datetime")
    uid = task.get("UID", "")
    return {
        "key": f"{task['calendar']}/{uid}",
        "uid": uid,
        "list": task["calendar"],
        "title": _single_line(task.get("SUMMARY") or "Untitled task"),
        "description": _single_line(task.get("DESCRIPTION", "")),
        "due": _iso_utc(due) if due else None,
        "status": task.get("STATUS", "NEEDS-ACTION"),
        "percent_complete": _int_or_default(task.get("PERCENT-COMPLETE"), 0),
        "priority": _int_or_default(task.get("PRIORITY"), None),
        "categories": _split_categories(task.get("CATEGORIES")),
        "overdue": overdue,
        "reminder_minutes_before": task.get("reminder_minutes_before", 0),
        "last_modified": _iso_property_utc(task.get("LAST-MODIFIED")),
    }


def _iso_utc(value: datetime.datetime) -> str:
    return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_property_utc(value: str | None) -> str | None:
    parsed = _parse_ical_datetime(value, None, datetime.UTC)
    return _iso_utc(parsed) if parsed else None


def _int_or_default(value: str | None, default: int | None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _split_categories(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _selector_set(selector: str | None) -> frozenset[str]:
    if not selector:
        return frozenset()
    return frozenset(
        item.strip().casefold() for item in selector.split(",") if item.strip()
    )


def _calendar_matches(calendar: Calendar, wanted: frozenset[str]) -> bool:
    return bool(wanted & {calendar.name.casefold(), calendar.slug.casefold()})


_ALARM_DURATION_RE = re.compile(
    r"^(?P<sign>[-+]?)P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def _trigger_minutes(value: str) -> int:
    """Minutes-before-start for an iCalendar VALARM TRIGGER duration.

    Returns 0 for at/after triggers or absolute (VALUE=DATE-TIME) triggers.
    Example: _trigger_minutes("-PT10M") -> 10.
    """
    match = _ALARM_DURATION_RE.match(value.strip())
    if not match or match.group("sign") != "-":
        return 0
    weeks = int(match.group("weeks") or 0)
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    return ((weeks * 7 + days) * 24 + hours) * 60 + minutes


def _alarm_minutes_by_uid(text: str, component_name: str) -> dict[str, int]:
    """Map each component UID to its first VALARM lead in minutes-before.

    Kept separate from _parse_ical_components, which intentionally drops nested
    VALARM data. Example: {"task-uid": 10} for a VTODO with TRIGGER -PT10M.
    """
    result: dict[str, int] = {}
    uid: str | None = None
    trigger: int | None = None
    in_component = in_alarm = False
    for line in _unfold_ical(text):
        upper = line.upper()
        if upper == f"BEGIN:{component_name}":
            in_component, uid, trigger = True, None, None
        elif upper == f"END:{component_name}":
            if uid is not None and uid not in result:
                result[uid] = trigger or 0
            in_component = False
        elif not in_component:
            continue
        elif upper == "BEGIN:VALARM":
            in_alarm = True
        elif upper == "END:VALARM":
            in_alarm = False
        elif not in_alarm and upper.startswith("UID:"):
            uid = line.split(":", 1)[1]
        elif in_alarm and trigger is None and upper.startswith("TRIGGER"):
            trigger = _trigger_minutes(line.split(":", 1)[1])
    return result


def _unfold_ical(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded = []
    for line in lines:
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _default_ca_bundle() -> bool | str:
    return (
        os.getenv("NC_CA_BUNDLE")
        or os.getenv("REQUESTS_CA_BUNDLE")
        or ssl.get_default_verify_paths().cafile
        or True
    )


def _parse_ical_components(text: str, component_name: str) -> list[dict]:
    components = []
    component = None
    nested_depth = 0
    for line in _unfold_ical(text):
        upper = line.upper()
        if upper == f"BEGIN:{component_name}":
            component = {}
            nested_depth = 0
            continue
        if component is None:
            continue
        if upper.startswith("BEGIN:"):
            nested_depth += 1
            continue
        if upper.startswith("END:") and nested_depth:
            nested_depth -= 1
            continue
        if upper == f"END:{component_name}":
            components.append(component)
            component = None
            continue
        if nested_depth or ":" not in line:
            continue

        raw_name, value = line.split(":", 1)
        name, _, parameters = raw_name.partition(";")
        name = name.upper()
        component[name] = _unescape_ical_text(value)
        if parameters:
            component[f"{name}_parameters"] = parameters
    return components


def _parse_vtodos(text: str) -> list[dict]:
    return _parse_ical_components(text, "VTODO")


def _match_tasks(tasks: list[dict], identifier: str) -> list[dict]:
    wanted = _single_line(identifier).strip().casefold()
    exact = [
        item
        for item in tasks
        if wanted
        in {
            (item.get("SUMMARY") or "").strip().casefold(),
            (item.get("UID") or "").casefold(),
            (item.get("UID") or "")[:8].casefold(),
        }
    ]
    if exact:
        return exact
    uid_matches = []
    for item in tasks:
        uid = item.get("UID") or ""
        if uid and (uid.casefold() in wanted or uid[:8].casefold() in wanted):
            uid_matches.append(item)
    if uid_matches:
        return uid_matches
    return [
        item
        for item in tasks
        if (item.get("SUMMARY") or "").strip()
        and (item.get("SUMMARY") or "").strip().casefold() in wanted
    ]


def _mark_vtodo_completed(
    text: str,
    uid: str,
    completed_at: datetime.datetime,
) -> str:
    timestamp = _format_utc(completed_at)
    return _rewrite_vtodo_properties(
        text,
        uid,
        {
            "STATUS": "STATUS:COMPLETED",
            "PERCENT-COMPLETE": "PERCENT-COMPLETE:100",
            "COMPLETED": f"COMPLETED:{timestamp}",
        },
        completed_at,
    )


def _rewrite_vtodo_properties(
    text: str,
    uid: str,
    replacements: dict[str, str | None],
    modified_at: datetime.datetime,
) -> str:
    lines = _unfold_ical(text)
    output = []
    index = 0
    found = False
    while index < len(lines):
        if lines[index].upper() != "BEGIN:VTODO":
            if lines[index]:
                output.append(lines[index])
            index += 1
            continue

        start = index
        nested_depth = 0
        index += 1
        while index < len(lines):
            upper = lines[index].upper()
            if upper.startswith("BEGIN:"):
                nested_depth += 1
            elif upper.startswith("END:") and nested_depth:
                nested_depth -= 1
            elif upper == "END:VTODO":
                break
            index += 1
        if index >= len(lines):
            raise NextcloudError("Nextcloud task contains an incomplete VTODO")

        block = lines[start : index + 1]
        parsed = _parse_vtodos("\r\n".join(block))
        if not parsed or parsed[0].get("UID") != uid:
            output.extend(block)
            index += 1
            continue

        sequence = 0
        try:
            sequence = int(parsed[0].get("SEQUENCE", "0"))
        except ValueError:
            pass
        replacement_names = {name.upper() for name in replacements}
        replacement_names.update({"LAST-MODIFIED", "SEQUENCE"})
        replacement_lines = [
            line for line in replacements.values() if line is not None
        ]
        replacement_lines.extend(
            [
                f"LAST-MODIFIED:{_format_utc(modified_at)}",
                f"SEQUENCE:{sequence + 1}",
            ]
        )

        output.append(block[0])
        nested_depth = 0
        inserted = False
        for line in block[1:-1]:
            upper = line.upper()
            if not inserted and nested_depth == 0 and upper.startswith("BEGIN:"):
                output.extend(replacement_lines)
                inserted = True
            property_name = line.split(":", 1)[0].split(";", 1)[0].upper()
            if nested_depth != 0 or property_name not in replacement_names:
                output.append(line)
            if upper.startswith("BEGIN:"):
                nested_depth += 1
            elif upper.startswith("END:") and nested_depth:
                nested_depth -= 1
        if not inserted:
            output.extend(replacement_lines)
        output.append("END:VTODO")
        found = True
        index += 1

    if not found:
        raise NextcloudError(f"Nextcloud resource does not contain task UID: {uid}")
    return _serialize_ical(output)


def _set_vtodo_alarm(
    text: str,
    uid: str,
    title: str,
    trigger: str,
    modified_at: datetime.datetime,
) -> str:
    rewritten = _rewrite_vtodo_properties(text, uid, {}, modified_at)
    lines = _unfold_ical(rewritten)
    output = []
    index = 0
    found = False
    while index < len(lines):
        if lines[index].upper() != "BEGIN:VTODO":
            if lines[index]:
                output.append(lines[index])
            index += 1
            continue

        start = index
        index += 1
        nested_depth = 0
        while index < len(lines):
            upper = lines[index].upper()
            if upper.startswith("BEGIN:"):
                nested_depth += 1
            elif upper.startswith("END:") and nested_depth:
                nested_depth -= 1
            elif upper == "END:VTODO":
                break
            index += 1
        if index >= len(lines):
            raise NextcloudError("Nextcloud task contains an incomplete VTODO")

        block = lines[start : index + 1]
        parsed = _parse_vtodos("\r\n".join(block))
        if not parsed or parsed[0].get("UID") != uid:
            output.extend(block)
            index += 1
            continue

        output.append("BEGIN:VTODO")
        block_index = 1
        while block_index < len(block) - 1:
            if block[block_index].upper() != "BEGIN:VALARM":
                output.append(block[block_index])
                block_index += 1
                continue
            alarm_end = block_index + 1
            while (
                alarm_end < len(block) - 1
                and block[alarm_end].upper() != "END:VALARM"
            ):
                alarm_end += 1
            if alarm_end >= len(block) - 1:
                raise NextcloudError("Nextcloud task contains an incomplete VALARM")
            alarm_block = block[block_index : alarm_end + 1]
            if not any(
                line.upper() == "X-SERVITOR-REMINDER:TRUE" for line in alarm_block
            ):
                output.extend(alarm_block)
            block_index = alarm_end + 1
        output.extend(_task_alarm_lines(title, trigger))
        output.append("END:VTODO")
        found = True
        index += 1

    if not found:
        raise NextcloudError(f"Nextcloud resource does not contain task UID: {uid}")
    return _serialize_ical(output)


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _extract_calendar_timezone(value: str) -> str | None:
    for line in _unfold_ical(value):
        if line.upper().startswith("TZID:"):
            timezone = line.split(":", 1)[1].strip()
            try:
                ZoneInfo(timezone)
                return timezone
            except ZoneInfoNotFoundError:
                return None
    return None


def _unescape_ical_text(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _escape_ical_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _parse_user_datetime(value: str, timezone: ZoneInfo) -> datetime.datetime:
    try:
        parsed = datetime.datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except (AttributeError, ValueError) as error:
        raise NextcloudError(
            "due_at must use ISO format, for example 2026-08-05 18:00:00"
        ) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(datetime.UTC).replace(microsecond=0)


def _parse_ical_datetime(
    value: str | None,
    parameters: str | None,
    default_timezone: ZoneInfo,
) -> datetime.datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=datetime.UTC
            )
        if "T" in value:
            parsed = datetime.datetime.strptime(value, "%Y%m%dT%H%M%S")
        else:
            parsed = datetime.datetime.strptime(value, "%Y%m%d")
        timezone = default_timezone
        if parameters:
            for parameter in parameters.split(";"):
                if parameter.upper().startswith("TZID="):
                    timezone = ZoneInfo(parameter.split("=", 1)[1])
                    break
        return parsed.replace(tzinfo=timezone).astimezone(datetime.UTC)
    except (ValueError, ZoneInfoNotFoundError):
        return None


def _display_datetime(
    value: datetime.datetime | None, timezone: ZoneInfo
) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone).strftime("%Y-%m-%d %H:%M %Z")


def _alarm_trigger(minutes_before: int) -> str:
    if minutes_before == 0:
        return "PT0M"
    return f"-PT{minutes_before}M"


def _reminder_id_for_task(username: str, task_uid: str) -> str:
    if task_uid.endswith("@servitor"):
        candidate = task_uid.removesuffix("@servitor")
        try:
            return str(uuid.UUID(candidate))
        except ValueError:
            pass
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{username}|{task_uid}"))


def _task_alarm_lines(title: str, trigger: str) -> list[str]:
    return [
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_escape_ical_text('Task reminder: ' + title)}",
        f"TRIGGER;RELATED=END:{trigger}",
        "X-SERVITOR-REMINDER:TRUE",
        "END:VALARM",
    ]


def _format_utc(value: datetime.datetime) -> str:
    return value.astimezone(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")


def _format_calendar_datetime(
    property_name: str,
    value: datetime.datetime,
    timezone_name: str,
) -> str:
    if timezone_name == "UTC":
        return f"{property_name}:{_format_utc(value)}"
    timezone = ZoneInfo(timezone_name)
    local_value = value.astimezone(timezone).strftime("%Y%m%dT%H%M%S")
    return f"{property_name};TZID={timezone_name}:{local_value}"


def _fold_ical_line(line: str) -> list[str]:
    result = []
    current = ""
    limit = 75
    for character in line:
        if len((current + character).encode("utf-8")) > limit:
            result.append(current)
            current = " " + character
            limit = 75
        else:
            current += character
    result.append(current)
    return result


def _serialize_ical(lines: list[str]) -> str:
    folded = []
    for line in lines:
        folded.extend(_fold_ical_line(line))
    return "\r\n".join(folded) + "\r\n"


def _build_task_ics(
    uid: str,
    title: str,
    description: str,
    due: datetime.datetime,
    now: datetime.datetime,
    trigger: str,
) -> str:
    timestamp = _format_utc(now)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ServitorAssistant//Nextcloud Tasks//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VTODO",
        f"UID:{uid}",
        f"DTSTAMP:{timestamp}",
        f"CREATED:{timestamp}",
        f"LAST-MODIFIED:{timestamp}",
        f"SUMMARY:{_escape_ical_text(title)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_escape_ical_text(description)}")
    lines.extend([f"DUE:{_format_utc(due)}", "STATUS:NEEDS-ACTION"])
    lines.extend(_task_alarm_lines(title, trigger))
    lines.extend(["END:VTODO", "END:VCALENDAR"])
    return _serialize_ical(lines)


def _build_reminder_event_ics(
    uid: str,
    task_uid: str,
    title: str,
    description: str,
    due: datetime.datetime,
    now: datetime.datetime,
    trigger: str,
    timezone_name: str,
) -> str:
    timestamp = _format_utc(now)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ServitorAssistant//Nextcloud Tasks//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{timestamp}",
        f"CREATED:{timestamp}",
        f"LAST-MODIFIED:{timestamp}",
        _format_calendar_datetime("DTSTART", due, timezone_name),
        _format_calendar_datetime(
            "DTEND", due + datetime.timedelta(minutes=5), timezone_name
        ),
        f"SUMMARY:{_escape_ical_text('Task reminder: ' + title)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_escape_ical_text(description)}")
    lines.extend(
        [
            "STATUS:CONFIRMED",
            "TRANSP:TRANSPARENT",
            f"RELATED-TO;RELTYPE=PARENT:{task_uid}",
            f"X-SERVITOR-TASK-UID:{task_uid}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape_ical_text('Task reminder: ' + title)}",
            f"TRIGGER;RELATED=START:{trigger}",
            "END:VALARM",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )
    return _serialize_ical(lines)
