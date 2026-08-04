import html
import unittest
from typing import cast

import requests

from api.mcp_module.stremable_http.nextcloud_tasks import (
    MAX_TOOL_OUTPUT_CHARS,
    NextcloudError,
    NextcloudTasksClient,
    _parse_vtodos,
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
):
    component_xml = "".join(f'<c:comp name="{item}"/>' for item in components)
    timezone_ics = html.escape(
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VTIMEZONE\r\n"
        f"TZID:{timezone}\r\n"
        "END:VTIMEZONE\r\n"
        "END:VCALENDAR\r\n"
    )
    return f"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/remote.php/dav/calendars/vitor/tasks/</d:href>
    <d:propstat><d:prop>
      <d:displayname>{html.escape(name)}</d:displayname>
      <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
      <d:current-user-privilege-set>
        <d:privilege><d:write/></d:privilege>
      </d:current-user-privilege-set>
      <c:supported-calendar-component-set>{component_xml}</c:supported-calendar-component-set>
      <c:calendar-timezone>{timezone_ics}</c:calendar-timezone>
    </d:prop></d:propstat>
  </d:response>
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
        self.assertNotIn("BEGIN:VALARM", task_ics)
        self.assertIn("BEGIN:VEVENT", event_ics)
        self.assertIn("DTSTART;TZID=America/Recife:20990101T120000", event_ics)
        self.assertIn("TRANSP:TRANSPARENT", event_ics)
        self.assertIn("TRIGGER;RELATED=START:-PT15M", event_ics)
        self.assertIn("X-SERVITOR-TASK-UID:", event_ics)
        self.assertTrue(all(len(line.encode()) <= 75 for line in task_ics.split("\r\n")))
        self.assertEqual(session.requests[2][2]["headers"]["If-None-Match"], "*")

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
