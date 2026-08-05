import html
import datetime
import unittest
from typing import cast
from unittest.mock import patch

import requests

from api.mcp_module.stremable_http.nextcloud_tasks import (
    MAX_TOOL_OUTPUT_CHARS,
    NextcloudError,
    NextcloudTasksClient,
    _mark_vtodo_completed,
    _parse_vtodos,
    _set_vtodo_alarm,
)


class FakeResponse:
    def __init__(self, content=b"", status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.auth = None
        self.headers = {}

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"Unexpected request: {method} {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def calendar_response(
    *,
    name="Tasks",
    components=("VTODO", "VEVENT"),
    timezone="America/Recife",
    slug="tasks",
):
    return calendars_response(
        [
            {
                "name": name,
                "components": components,
                "timezone": timezone,
                "slug": slug,
            }
        ]
    )


def calendars_response(calendars):
    responses = []
    for calendar in calendars:
        name = calendar["name"]
        components = calendar.get("components", ("VTODO", "VEVENT"))
        timezone = calendar.get("timezone", "America/Recife")
        slug = calendar.get("slug", name.casefold())
        component_xml = "".join(f'<c:comp name="{item}"/>' for item in components)
        timezone_ics = html.escape(
            "BEGIN:VCALENDAR\r\n"
            "BEGIN:VTIMEZONE\r\n"
            f"TZID:{timezone}\r\n"
            "END:VTIMEZONE\r\n"
            "END:VCALENDAR\r\n"
        )
        responses.append(f"""
  <d:response>
    <d:href>/remote.php/dav/calendars/vitor/{slug}/</d:href>
    <d:propstat><d:prop>
      <d:displayname>{html.escape(name)}</d:displayname>
      <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
      <d:current-user-privilege-set>
        <d:privilege><d:write/></d:privilege>
      </d:current-user-privilege-set>
      <c:supported-calendar-component-set>{component_xml}</c:supported-calendar-component-set>
      <c:calendar-timezone>{timezone_ics}</c:calendar-timezone>
    </d:prop></d:propstat>
  </d:response>""")
    return f"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  {''.join(responses)}
</d:multistatus>""".encode()


def task_report(tasks):
    responses = []
    for index, task in enumerate(tasks):
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "BEGIN:VTODO\r\n"
            f"UID:{task.get('uid', index)}\r\n"
            f"SUMMARY:{task['title']}\r\n"
            f"DESCRIPTION:{task.get('description', '')}\r\n"
            f"DUE:{task.get('due', '20990101T120000Z')}\r\n"
            f"STATUS:{task.get('status', 'NEEDS-ACTION')}\r\n"
            "END:VTODO\r\n"
            "END:VCALENDAR\r\n"
        )
        responses.append(
            "<d:response>"
            f"<d:href>/tasks/{index}.ics</d:href>"
            "<d:propstat><d:prop>"
            f"<d:getetag>&quot;etag-{index}&quot;</d:getetag>"
            f"<c:calendar-data>{html.escape(ics)}</c:calendar-data>"
            "</d:prop></d:propstat>"
            "</d:response>"
        )
    return (
        '<?xml version="1.0"?>'
        '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        + "".join(responses)
        + "</d:multistatus>"
    ).encode()


def event_report(events):
    responses = []
    for index, event in enumerate(events):
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{event.get('uid', index)}\r\n"
            f"SUMMARY:{event['title']}\r\n"
            f"DESCRIPTION:{event.get('description', '')}\r\n"
            f"DTSTART{event.get('start_parameters', '')}:{event['start']}\r\n"
            f"DTEND{event.get('end_parameters', '')}:{event['end']}\r\n"
            f"STATUS:{event.get('status', 'CONFIRMED')}\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        responses.append(
            "<d:response>"
            f"<d:href>/events/{index}.ics</d:href>"
            "<d:propstat><d:prop>"
            f"<c:calendar-data>{html.escape(ics)}</c:calendar-data>"
            "</d:prop></d:propstat>"
            "</d:response>"
        )
    return (
        '<?xml version="1.0"?>'
        '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        + "".join(responses)
        + "</d:multistatus>"
    ).encode()


class NextcloudTasksClientTest(unittest.TestCase):
    def make_client(self, responses):
        session = FakeSession(responses)
        client = NextcloudTasksClient(
            "https://cloud.example.com",
            "vitor",
            "secret",
            session=cast(requests.Session, session),
        )
        return client, session

    def test_rejects_insecure_nextcloud_url(self):
        with self.assertRaisesRegex(NextcloudError, "must use HTTPS"):
            NextcloudTasksClient("http://cloud.example.com", "vitor", "secret")

    def test_allows_explicit_tls_verification_override(self):
        client, session = self.make_client([])
        insecure_client = NextcloudTasksClient(
            "https://cloud.example.com",
            "vitor",
            "secret",
            tls_verify=False,
            session=cast(requests.Session, session),
        )

        self.assertFalse(insecure_client.session.verify)

    def test_tls_error_explains_available_configuration(self):
        client, _ = self.make_client(
            [requests.exceptions.SSLError("certificate verify failed")]
        )

        with self.assertRaisesRegex(NextcloudError, "configure NC_CA_BUNDLE"):
            client.discover_calendars()

    def test_parser_ignores_valarm_fields(self):
        data = """BEGIN:VCALENDAR\r
BEGIN:VTODO\r
UID:task-1\r
SUMMARY:Main title\r
DESCRIPTION:Task description\r
BEGIN:VALARM\r
DESCRIPTION:Alarm description\r
ACTION:DISPLAY\r
END:VALARM\r
END:VTODO\r
END:VCALENDAR\r
"""
        self.assertEqual(
            _parse_vtodos(data),
            [
                {
                    "UID": "task-1",
                    "SUMMARY": "Main title",
                    "DESCRIPTION": "Task description",
                }
            ],
        )

    def test_list_tasks_bounds_count_and_description(self):
        long_description = "x" * 500
        tasks = [
            {
                "uid": f"task-{index}",
                "title": f"Task {index}",
                "description": long_description,
            }
            for index in range(25)
        ]
        client, session = self.make_client(
            [
                FakeResponse(calendar_response(), 207),
                FakeResponse(task_report(tasks), 207),
            ]
        )

        result = client.list_tasks(limit=100)

        self.assertLessEqual(len(result), MAX_TOOL_OUTPUT_CHARS)
        self.assertIn("Showing 20 of 25", result)
        self.assertIn("additional tasks omitted", result)
        self.assertNotIn(long_description, result)
        report_body = session.requests[1][2]["data"].decode()
        self.assertIn('<c:prop name="SUMMARY"/>', report_body)
        self.assertIn('<c:prop name="DESCRIPTION"/>', report_body)
        self.assertIn('<c:prop-filter name="COMPLETED">', report_body)
        self.assertNotIn('<c:prop-filter name="STATUS">', report_body)

    def test_list_events_uses_exact_time_and_labels_day_events(self):
        client, session = self.make_client(
            [
                FakeResponse(calendar_response(name="Personal", components=("VEVENT",)), 207),
                FakeResponse(
                    event_report(
                        [
                            {
                                "title": "All-day note",
                                "start": "20260804",
                                "end": "20260805",
                                "start_parameters": ";VALUE=DATE",
                                "end_parameters": ";VALUE=DATE",
                            },
                            {
                                "title": "Past meeting",
                                "start": "20260804T130000Z",
                                "end": "20260804T140000Z",
                            },
                            {
                                "title": "Current meeting",
                                "start": "20260804T150000Z",
                                "end": "20260804T160000Z",
                            },
                            {
                                "title": "Next meeting",
                                "start": "20260804T170000Z",
                                "end": "20260804T180000Z",
                            },
                        ]
                    ),
                    207,
                ),
            ]
        )

        class FixedDateTime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2026, 8, 4, 15, 30, 45, tzinfo=datetime.UTC)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)

        with patch(
            "api.mcp_module.stremable_http.nextcloud_tasks.datetime.datetime",
            FixedDateTime,
        ):
            result = client.list_events()

        self.assertIn("current time 12:30:45", result)
        self.assertIn("[today] all day | All-day note", result)
        self.assertIn("[ended] 10:00-11:00 | Past meeting", result)
        self.assertIn("[ongoing] 12:00-13:00 | Current meeting", result)
        self.assertIn("[upcoming] 14:00-15:00 | Next meeting", result)
        report_body = session.requests[1][2]["data"].decode()
        self.assertIn('<c:expand start="20260804T030000Z" end="20260805T030000Z"/>', report_body)
        self.assertIn('<c:time-range start="20260804T030000Z" end="20260805T030000Z"/>', report_body)

    def test_list_events_rejects_invalid_date(self):
        client, _ = self.make_client([])

        with self.assertRaisesRegex(NextcloudError, "YYYY-MM-DD"):
            client.list_events("today")

    def test_list_events_bounds_count_and_description(self):
        long_description = "x" * 500
        events = [
            {
                "title": f"Event {index}",
                "description": long_description,
                "start": f"20990101T{index:02d}0000Z",
                "end": f"20990101T{index:02d}3000Z",
            }
            for index in range(24)
        ]
        client, _ = self.make_client(
            [
                FakeResponse(calendar_response(components=("VEVENT",)), 207),
                FakeResponse(event_report(events), 207),
            ]
        )

        result = client.list_events("2099-01-01", limit=100)

        self.assertLessEqual(len(result), MAX_TOOL_OUTPUT_CHARS)
        self.assertIn("showing 20 of 24 events", result)
        self.assertIn("additional events omitted", result)
        self.assertNotIn(long_description, result)

    def test_mark_vtodo_completed_preserves_nested_alarm(self):
        source = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "BEGIN:VTODO\r\n"
            "UID:task-1\r\n"
            "SUMMARY:Test\r\n"
            "STATUS:NEEDS-ACTION\r\n"
            "PERCENT-COMPLETE:0\r\n"
            "SEQUENCE:2\r\n"
            "BEGIN:VALARM\r\n"
            "ACTION:DISPLAY\r\n"
            "DESCRIPTION:Keep this alarm\r\n"
            "END:VALARM\r\n"
            "END:VTODO\r\n"
            "END:VCALENDAR\r\n"
        )

        result = _mark_vtodo_completed(
            source,
            "task-1",
            datetime.datetime(2026, 8, 4, 15, 0, tzinfo=datetime.UTC),
        )

        self.assertIn("STATUS:COMPLETED", result)
        self.assertIn("PERCENT-COMPLETE:100", result)
        self.assertIn("COMPLETED:20260804T150000Z", result)
        self.assertIn("LAST-MODIFIED:20260804T150000Z", result)
        self.assertIn("SEQUENCE:3", result)
        self.assertIn("DESCRIPTION:Keep this alarm", result)
        self.assertNotIn("STATUS:NEEDS-ACTION", result)

    def test_complete_task_updates_exact_title_with_etag(self):
        full_task = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "BEGIN:VTODO\r\n"
            "UID:onshape-1\r\n"
            "SUMMARY:Curso Onshape\r\n"
            "STATUS:NEEDS-ACTION\r\n"
            "END:VTODO\r\n"
            "END:VCALENDAR\r\n"
        ).encode()
        client, session = self.make_client(
            [
                FakeResponse(calendar_response(), 207),
                FakeResponse(
                    task_report([{"uid": "onshape-1", "title": "Curso Onshape"}]),
                    207,
                ),
                FakeResponse(full_task, 200),
                FakeResponse(status_code=204),
            ]
        )

        result = client.complete_task(
            "marca a task do Nextcloud Curso Onshape como done"
        )

        self.assertEqual(
            [item[0] for item in session.requests],
            ["PROPFIND", "REPORT", "GET", "PUT"],
        )
        self.assertIn("Nextcloud task completed [onshape-]", result)
        put_request = session.requests[3][2]
        self.assertEqual(put_request["headers"]["If-Match"], '"etag-0"')
        self.assertIn("STATUS:COMPLETED", put_request["data"].decode())

    def test_complete_task_rejects_concurrent_change(self):
        full_task = (
            "BEGIN:VCALENDAR\r\n"
            "BEGIN:VTODO\r\n"
            "UID:task-1\r\n"
            "SUMMARY:Test\r\n"
            "END:VTODO\r\n"
            "END:VCALENDAR\r\n"
        ).encode()
        client, _ = self.make_client(
            [
                FakeResponse(calendar_response(), 207),
                FakeResponse(task_report([{"uid": "task-1", "title": "Test"}]), 207),
                FakeResponse(full_task, 200),
                FakeResponse(status_code=412),
            ]
        )

        with self.assertRaisesRegex(NextcloudError, "changed concurrently"):
            client.complete_task("task-1")

    def test_list_tasks_filters_exact_calendar(self):
        client, session = self.make_client(
            [
                FakeResponse(
                    calendars_response(
                        [
                            {"name": "Tasks", "slug": "tasks"},
                            {"name": "TrabalhoFNDE", "slug": "trabalhofnde"},
                        ]
                    ),
                    207,
                ),
                FakeResponse(task_report([{"uid": "work-1", "title": "Work"}]), 207),
            ]
        )

        result = client.list_tasks(calendar="TrabalhoFNDE")

        self.assertIn("list TrabalhoFNDE", result)
        self.assertIn("/trabalhofnde/", session.requests[1][1])

    def test_get_task_returns_full_compact_details(self):
        client, _ = self.make_client(
            [
                FakeResponse(calendar_response(name="TrabalhoFNDE"), 207),
                FakeResponse(
                    task_report(
                        [
                            {
                                "uid": "work-1",
                                "title": "Work",
                                "description": "Details",
                                "due": "20990101T120000Z",
                                "status": "IN-PROCESS",
                            }
                        ]
                    ),
                    207,
                ),
            ]
        )

        result = client.get_task("work-1")

        self.assertIn("Title: Work", result)
        self.assertIn("List: TrabalhoFNDE", result)
        self.assertIn("Status: IN-PROCESS", result)
        self.assertIn("Description: Details", result)

    def test_update_task_rewrites_requested_fields_only(self):
        full_task = (
            "BEGIN:VCALENDAR\r\n"
            "BEGIN:VTODO\r\n"
            "UID:task-1\r\n"
            "SUMMARY:Old title\r\n"
            "DESCRIPTION:Old description\r\n"
            "DUE:20990101T120000Z\r\n"
            "STATUS:NEEDS-ACTION\r\n"
            "END:VTODO\r\n"
            "END:VCALENDAR\r\n"
        ).encode()
        client, session = self.make_client(
            [
                FakeResponse(calendar_response(), 207),
                FakeResponse(task_report([{"uid": "task-1", "title": "Old title"}]), 207),
                FakeResponse(full_task, 200),
                FakeResponse(status_code=204),
            ]
        )

        result = client.update_task(
            "task-1",
            title="New title",
            description="",
            due_at="",
            status="in process",
        )

        updated = session.requests[3][2]["data"].decode()
        self.assertIn("Nextcloud task updated", result)
        self.assertIn("SUMMARY:New title", updated)
        self.assertIn("STATUS:IN-PROCESS", updated)
        self.assertIn("PERCENT-COMPLETE:50", updated)
        self.assertNotIn("DESCRIPTION:", updated)
        self.assertNotIn("DUE:", updated)
        self.assertEqual(session.requests[3][2]["headers"]["If-Match"], '"etag-0"')

    def test_delete_task_uses_etag(self):
        client, session = self.make_client(
            [
                FakeResponse(calendar_response(), 207),
                FakeResponse(task_report([{"uid": "task-1", "title": "Delete me"}]), 207),
                FakeResponse(status_code=204),
            ]
        )

        result = client.delete_task("task-1")

        self.assertIn("Nextcloud task deleted", result)
        self.assertEqual([item[0] for item in session.requests], ["PROPFIND", "REPORT", "DELETE"])
        self.assertEqual(session.requests[2][2]["headers"]["If-Match"], '"etag-0"')

    def test_move_task_uses_webdav_move_and_destination(self):
        calendars = calendars_response(
            [
                {"name": "Tasks", "slug": "tasks"},
                {"name": "TrabalhoFNDE", "slug": "trabalhofnde"},
            ]
        )
        client, session = self.make_client(
            [
                FakeResponse(calendars, 207),
                FakeResponse(task_report([{"uid": "task-1", "title": "Move me"}]), 207),
                FakeResponse(calendars, 207),
                FakeResponse(status_code=201),
            ]
        )

        result = client.move_task("task-1", "TrabalhoFNDE", calendar="Tasks")

        self.assertIn("from Tasks to TrabalhoFNDE", result)
        self.assertEqual(session.requests[3][0], "MOVE")
        self.assertIn(
            "/trabalhofnde/0.ics",
            session.requests[3][2]["headers"]["Destination"],
        )
        self.assertEqual(session.requests[3][2]["headers"]["Overwrite"], "F")

    def test_create_task_writes_vtodo_and_linked_event(self):
        client, session = self.make_client(
            [
                FakeResponse(calendar_response(), 207),
                FakeResponse(task_report([]), 207),
                FakeResponse(status_code=201),
                FakeResponse(status_code=201),
            ]
        )

        result = client.create_task(
            "Comprar café, açúcar",
            "2099-01-01 12:00:00",
            "Descrição longa",
            15,
        )

        self.assertIn("Nextcloud task created", result)
        self.assertEqual([item[0] for item in session.requests], ["PROPFIND", "REPORT", "PUT", "PUT"])
        task_ics = session.requests[2][2]["data"].decode()
        event_ics = session.requests[3][2]["data"].decode()
        self.assertIn("BEGIN:VTODO", task_ics)
        self.assertIn("SUMMARY:Comprar café\\, açúcar", task_ics)
        self.assertIn("BEGIN:VALARM", task_ics)
        self.assertIn("TRIGGER;RELATED=END:-PT15M", task_ics)
        self.assertIn("X-SERVITOR-REMINDER:TRUE", task_ics)
        self.assertIn("BEGIN:VEVENT", event_ics)
        self.assertIn("DTSTART;TZID=America/Recife:20990101T120000", event_ics)
        self.assertIn("TRANSP:TRANSPARENT", event_ics)
        self.assertIn("TRIGGER;RELATED=START:-PT15M", event_ics)
        self.assertIn("X-SERVITOR-TASK-UID:", event_ics)
        self.assertTrue(all(len(line.encode()) <= 75 for line in task_ics.split("\r\n")))
        self.assertEqual(session.requests[2][2]["headers"]["If-None-Match"], "*")

    def test_set_task_reminder_updates_task_and_linked_event(self):
        full_task = (
            "BEGIN:VCALENDAR\r\n"
            "BEGIN:VTODO\r\n"
            "UID:task-1\r\n"
            "SUMMARY:Existing task\r\n"
            "DUE:20990101T120000Z\r\n"
            "BEGIN:VALARM\r\n"
            "ACTION:DISPLAY\r\n"
            "DESCRIPTION:Native alarm\r\n"
            "TRIGGER;RELATED=END:-PT1H\r\n"
            "END:VALARM\r\n"
            "END:VTODO\r\n"
            "END:VCALENDAR\r\n"
        ).encode()
        client, session = self.make_client(
            [
                FakeResponse(calendar_response(), 207),
                FakeResponse(
                    task_report(
                        [
                            {
                                "uid": "task-1",
                                "title": "Existing task",
                                "due": "20990101T120000Z",
                            }
                        ]
                    ),
                    207,
                ),
                FakeResponse(full_task, 200),
                FakeResponse(calendar_response(), 207),
                FakeResponse(status_code=204),
                FakeResponse(status_code=204),
            ]
        )

        result = client.set_task_reminder("task-1", 30)

        self.assertIn("Nextcloud task reminder set", result)
        self.assertEqual(
            [item[0] for item in session.requests],
            ["PROPFIND", "REPORT", "GET", "PROPFIND", "PUT", "PUT"],
        )
        event_ics = session.requests[4][2]["data"].decode()
        task_ics = session.requests[5][2]["data"].decode()
        self.assertIn("TRIGGER;RELATED=START:-PT30M", event_ics)
        self.assertIn("TRIGGER;RELATED=END:-PT30M", task_ics)
        self.assertIn("DESCRIPTION:Native alarm", task_ics)
        self.assertEqual(task_ics.count("X-SERVITOR-REMINDER:TRUE"), 1)
        self.assertEqual(session.requests[5][2]["headers"]["If-Match"], '"etag-0"')

    def test_set_vtodo_alarm_replaces_only_servitor_alarm(self):
        source = (
            "BEGIN:VCALENDAR\r\n"
            "BEGIN:VTODO\r\n"
            "UID:task-1\r\n"
            "SUMMARY:Test\r\n"
            "DUE:20990101T120000Z\r\n"
            "END:VTODO\r\n"
            "END:VCALENDAR\r\n"
        )
        now = datetime.datetime(2026, 8, 4, 15, 0, tzinfo=datetime.UTC)

        first = _set_vtodo_alarm(source, "task-1", "Test", "-PT10M", now)
        second = _set_vtodo_alarm(first, "task-1", "Test", "-PT20M", now)

        self.assertEqual(second.count("X-SERVITOR-REMINDER:TRUE"), 1)
        self.assertNotIn("TRIGGER;RELATED=END:-PT10M", second)
        self.assertIn("TRIGGER;RELATED=END:-PT20M", second)

    def test_create_task_rolls_back_when_event_fails(self):
        client, session = self.make_client(
            [
                FakeResponse(calendar_response(), 207),
                FakeResponse(task_report([]), 207),
                FakeResponse(status_code=201),
                FakeResponse(status_code=500),
                FakeResponse(status_code=404),
                FakeResponse(status_code=404),
                FakeResponse(status_code=204),
            ]
        )

        with self.assertRaisesRegex(NextcloudError, "rollback was attempted"):
            client.create_task("Test", "2099-01-01 12:00:00")

        self.assertEqual(
            [item[0] for item in session.requests],
            ["PROPFIND", "REPORT", "PUT", "PUT", "GET", "DELETE", "DELETE"],
        )

    def test_create_task_recovers_when_put_response_is_ambiguous(self):
        client, session = self.make_client(
            [
                FakeResponse(calendar_response(), 207),
                FakeResponse(task_report([]), 207),
                FakeResponse(status_code=412),
                FakeResponse(b"task exists", 200),
                FakeResponse(status_code=412),
                FakeResponse(b"event exists", 200),
            ]
        )

        result = client.create_task("Test", "2099-01-01 12:00:00")

        self.assertIn("Nextcloud task created", result)
        self.assertEqual(
            [item[0] for item in session.requests],
            ["PROPFIND", "REPORT", "PUT", "GET", "PUT", "GET"],
        )

    def test_cancelled_task_is_not_returned_as_pending(self):
        client, _ = self.make_client(
            [
                FakeResponse(calendar_response(), 207),
                FakeResponse(
                    task_report([{"title": "Cancelled", "status": "CANCELLED"}]),
                    207,
                ),
            ]
        )

        self.assertEqual(client.list_tasks(), "No Nextcloud tasks found.")

    def test_due_date_requires_exact_local_datetime(self):
        client, _ = self.make_client([])

        with self.assertRaisesRegex(NextcloudError, "due_at must use ISO format"):
            client.create_task("Test", "2099-01-01")


if __name__ == "__main__":
    unittest.main()
