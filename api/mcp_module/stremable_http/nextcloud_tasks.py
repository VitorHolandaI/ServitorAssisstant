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

    def _calendar_query(self, calendar: Calendar, show_completed: bool) -> list[dict]:
        completed_filter = ""
        if not show_completed:
            completed_filter = """
        <c:prop-filter name="COMPLETED">
          <c:is-not-defined/>
        </c:prop-filter>
        <c:prop-filter name="STATUS">
          <c:text-match negate-condition="yes">CANCELLED</c:text-match>
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
          <c:prop name="DUE"/>
          <c:prop name="STATUS"/>
          <c:prop name="COMPLETED"/>
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
            calendar_data = response.findtext(f".//{{{CALDAV}}}calendar-data")
            if not calendar_data:
                continue
            for task in _parse_vtodos(calendar_data):
                if not show_completed and task.get("STATUS") == "CANCELLED":
                    continue
                task["calendar"] = calendar.name
                task["href"] = href
                task["due_datetime"] = _parse_ical_datetime(
                    task.get("DUE"), task.get("DUE_parameters"), self.timezone
                )
                tasks.append(task)
        return tasks

    def list_tasks(self, show_completed: bool = False, limit: int = 10) -> str:
        limit = max(1, min(int(limit), MAX_TASKS))
        calendars = self.discover_calendars()
        tasks = []
        for calendar in calendars:
            if calendar.components and "VTODO" not in calendar.components:
                continue
            tasks.extend(self._calendar_query(calendar, show_completed))

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
            calendars, "VTODO", self.task_calendar
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
        task_ics = _build_task_ics(task_uid, title, description or "", due, now)
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


def _parse_vtodos(text: str) -> list[dict]:
    tasks = []
    task = None
    nested_depth = 0
    for line in _unfold_ical(text):
        upper = line.upper()
        if upper == "BEGIN:VTODO":
            task = {}
            nested_depth = 0
            continue
        if task is None:
            continue
        if upper.startswith("BEGIN:"):
            nested_depth += 1
            continue
        if upper.startswith("END:") and nested_depth:
            nested_depth -= 1
            continue
        if upper == "END:VTODO":
            tasks.append(task)
            task = None
            continue
        if nested_depth or ":" not in line:
            continue

        raw_name, value = line.split(":", 1)
        name, _, parameters = raw_name.partition(";")
        name = name.upper()
        task[name] = _unescape_ical_text(value)
        if parameters:
            task[f"{name}_parameters"] = parameters
    return tasks


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
    lines.extend(
        [
            f"DUE:{_format_utc(due)}",
            "STATUS:NEEDS-ACTION",
            "END:VTODO",
            "END:VCALENDAR",
        ]
    )
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
