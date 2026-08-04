#!/usr/bin/env python3

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from dotenv import load_dotenv


DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def request(url, username, password, method="GET", body=None, headers=None):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    request_headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": "servitor-nextcloud-test/1.0",
        **(headers or {}),
    }
    req = urllib.request.Request(
        url,
        data=body.encode() if isinstance(body, str) else body,
        headers=request_headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def discover_calendars(base_url, username, password):
    body = """<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:displayname/>
    <d:resourcetype/>
    <c:supported-calendar-component-set/>
  </d:prop>
</d:propfind>"""
    user = urllib.parse.quote(username, safe="")
    home_url = f"{base_url}/remote.php/dav/calendars/{user}/"
    data = request(
        home_url,
        username,
        password,
        method="PROPFIND",
        body=body,
        headers={"Depth": "1", "Content-Type": "application/xml"},
    )
    root = ET.fromstring(data)
    calendars = []

    for response in root.findall(f"{{{DAV}}}response"):
        resource_type = response.find(f".//{{{DAV}}}resourcetype")
        if resource_type is None or resource_type.find(f"{{{CALDAV}}}calendar") is None:
            continue

        href = response.findtext(f"{{{DAV}}}href", default="")
        name = response.findtext(f".//{{{DAV}}}displayname", default=href)
        components = [
            element.get("name", "")
            for element in response.findall(
                f".//{{{CALDAV}}}supported-calendar-component-set/"
                f"{{{CALDAV}}}comp"
            )
            if element.get("name")
        ]
        calendars.append(
            {
                "name": name,
                "url": urllib.parse.urljoin(f"{base_url}/", href),
                "components": components,
            }
        )

    return calendars


def unfold_ical(text):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded = []
    for line in lines:
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def decode_ical_text(value):
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def parse_ical_components(text, component_name):
    components = []
    current = None

    for line in unfold_ical(text):
        if line == f"BEGIN:{component_name}":
            current = {}
            continue
        if line == f"END:{component_name}":
            if current is not None:
                components.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue

        raw_name, value = line.split(":", 1)
        name, _, parameters = raw_name.partition(";")
        value = decode_ical_text(value)
        if name in current:
            previous = current[name]
            current[name] = previous + [value] if isinstance(previous, list) else [previous, value]
        else:
            current[name] = value
        if parameters:
            current[f"{name}_parameters"] = parameters

    return components


def query_calendar(calendar_url, username, password, component_name):
    body = f"""<?xml version="1.0"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag/>
    <c:calendar-data/>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="{component_name}"/>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""
    data = request(
        calendar_url,
        username,
        password,
        method="REPORT",
        body=body,
        headers={"Depth": "1", "Content-Type": "application/xml"},
    )
    root = ET.fromstring(data)
    results = []

    for response in root.findall(f"{{{DAV}}}response"):
        href = response.findtext(f"{{{DAV}}}href", default="")
        etag = response.findtext(f".//{{{DAV}}}getetag")
        calendar_data = response.findtext(f".//{{{CALDAV}}}calendar-data")
        if not calendar_data:
            continue
        for component in parse_ical_components(calendar_data, component_name):
            component["href"] = href
            component["etag"] = etag
            results.append(component)

    return results


def get_notes(base_url, username, password):
    data = request(
        f"{base_url}/index.php/apps/notes/api/v1/notes",
        username,
        password,
        headers={"Accept": "application/json"},
    )
    return json.loads(data)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Print structured data from a Nextcloud account."
    )
    parser.add_argument(
        "section",
        choices=("task", "calendar", "notes"),
        help="data to fetch and print",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    missing = [
        name
        for name in ("NC_URL", "NC_USER", "NC_APP_PASSWORD")
        if not os.environ.get(name)
    ]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 2

    base_url = os.environ["NC_URL"].rstrip("/")
    username = os.environ["NC_USER"]
    password = os.environ["NC_APP_PASSWORD"]

    try:
        if args.section == "notes":
            notes = get_notes(base_url, username, password)
            print(
                json.dumps(
                    {"count": len(notes), "notes": notes},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        calendars = discover_calendars(base_url, username, password)
        component_name = "VTODO" if args.section == "task" else "VEVENT"
        item_name = "tasks" if args.section == "task" else "events"
        item_count = 0
        matching_calendars = []

        for calendar in calendars:
            supported = set(calendar["components"])
            if supported and component_name not in supported:
                continue
            calendar[item_name] = query_calendar(
                calendar["url"], username, password, component_name
            )
            item_count += len(calendar[item_name])
            matching_calendars.append(calendar)

        result = {
            "count": item_count,
            "calendars": matching_calendars,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except urllib.error.HTTPError as error:
        details = error.read().decode(errors="replace")
        print(f"Nextcloud returned HTTP {error.code}: {error.reason}", file=sys.stderr)
        if details:
            print(details, file=sys.stderr)
        return 1
    except (urllib.error.URLError, ET.ParseError, json.JSONDecodeError) as error:
        print(f"Could not read Nextcloud data: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
