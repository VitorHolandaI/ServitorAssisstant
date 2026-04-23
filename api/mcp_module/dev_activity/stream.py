import datetime as dt
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from requests import HTTPError
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(PROJECT_ROOT / "api" / ".env")

MCP_HOST = os.getenv("DEV_ACTIVITY_MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("DEV_ACTIVITY_MCP_PORT", "8002"))
TIMEZONE = os.getenv("DEV_ACTIVITY_TIMEZONE", "America/Recife")

mcp = FastMCP("WeeklyDevActivity", host=MCP_HOST, port=MCP_PORT, stateless_http=True)


@dataclass
class ActivityEvent:
    platform: str
    repo: str
    event_type: str
    action: str
    created_at: dt.datetime
    summary: str
    details: str


def _now_local() -> dt.datetime:
    return dt.datetime.now(ZoneInfo(TIMEZONE))


def _week_start(reference: dt.datetime | None = None) -> dt.datetime:
    current = reference or _now_local()
    return current.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=current.weekday())


def _parse_iso_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(ZoneInfo(TIMEZONE))


def _http_get_json(url: str, headers: dict[str, str], params: dict | None = None) -> list | dict:
    response = requests.get(url, headers=headers, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _gitea_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = os.getenv("GITEA_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _normalize_github_event(event: dict) -> ActivityEvent | None:
    created_at = _parse_iso_datetime(event.get("created_at"))
    if created_at is None:
        return None

    repo = event.get("repo", {}).get("name", "unknown")
    event_type = event.get("type", "UnknownEvent")
    payload = event.get("payload") or {}
    action = payload.get("action") or ""

    if event_type == "PushEvent":
        commit_count = len(payload.get("commits") or [])
        branch = (payload.get("ref") or "").split("/")[-1] or "unknown"
        summary = f"Push em {repo}"
        details = f"{commit_count} commit(s) na branch {branch}"
    elif event_type == "PullRequestEvent":
        pr_number = (payload.get("pull_request") or {}).get("number")
        summary = f"Pull request em {repo}"
        details = f"acao={action or 'unknown'} PR #{pr_number or '?'}"
    elif event_type == "IssuesEvent":
        issue_number = (payload.get("issue") or {}).get("number")
        summary = f"Issue em {repo}"
        details = f"acao={action or 'unknown'} issue #{issue_number or '?'}"
    elif event_type == "IssueCommentEvent":
        issue_number = (payload.get("issue") or {}).get("number")
        summary = f"Comentario em issue/pr de {repo}"
        details = f"acao={action or 'created'} item #{issue_number or '?'}"
    elif event_type == "CreateEvent":
        ref_type = payload.get("ref_type") or "resource"
        ref_name = payload.get("ref") or ""
        summary = f"Criacao em {repo}"
        details = f"{ref_type}: {ref_name or '(sem nome)'}"
    elif event_type == "DeleteEvent":
        ref_type = payload.get("ref_type") or "resource"
        ref_name = payload.get("ref") or ""
        summary = f"Remocao em {repo}"
        details = f"{ref_type}: {ref_name or '(sem nome)'}"
    elif event_type == "PullRequestReviewEvent":
        review_state = (payload.get("review") or {}).get("state") or action or "submitted"
        summary = f"Review de PR em {repo}"
        details = review_state
    else:
        summary = f"{event_type} em {repo}"
        details = action or "sem detalhes adicionais"

    return ActivityEvent(
        platform="GitHub",
        repo=repo,
        event_type=event_type,
        action=action,
        created_at=created_at,
        summary=summary,
        details=details,
    )


def _normalize_gitea_event(event: dict) -> ActivityEvent | None:
    created_at = _parse_iso_datetime(event.get("created"))
    if created_at is None:
        return None

    repo_info = event.get("repo") or {}
    owner = repo_info.get("owner_name") or repo_info.get("owner") or ""
    name = repo_info.get("name") or "unknown"
    repo = f"{owner}/{name}".strip("/") if owner else name
    event_type = event.get("op_type") or "unknown"
    ref_name = event.get("ref_name") or ""
    content = event.get("content") or ""
    action = str(event_type)

    summary = f"{event_type} em {repo}"
    details_parts = []
    if ref_name:
        details_parts.append(ref_name)
    if content:
        details_parts.append(content.replace("\n", " ")[:140])
    details = " | ".join(details_parts) if details_parts else "sem detalhes adicionais"

    return ActivityEvent(
        platform="Gitea",
        repo=repo,
        event_type=event_type,
        action=action,
        created_at=created_at,
        summary=summary,
        details=details,
    )


def _fetch_github_events(username: str, since: dt.datetime) -> list[ActivityEvent]:
    events: list[ActivityEvent] = []
    headers = _github_headers()
    api_base = os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/")

    for page in range(1, 4):
        payload = _http_get_json(
            f"{api_base}/users/{username}/events",
            headers=headers,
            params={"per_page": 100, "page": page},
        )
        if not isinstance(payload, list) or not payload:
            break

        reached_older_event = False
        for item in payload:
            actor = item.get("actor") or {}
            if actor.get("login", "").lower() != username.lower():
                continue

            normalized = _normalize_github_event(item)
            if normalized is None:
                continue
            if normalized.created_at < since:
                reached_older_event = True
                continue
            events.append(normalized)

        if reached_older_event:
            break

    return sorted(events, key=lambda item: item.created_at, reverse=True)


def _fetch_gitea_repos(username: str) -> list[dict]:
    repos: list[dict] = []
    base_url = os.getenv("GITEA_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
    headers = _gitea_headers()

    for page in range(1, 11):
        payload = _http_get_json(
            f"{base_url}/api/v1/users/{username}/repos",
            headers=headers,
            params={"limit": 100, "page": page},
        )
        if not isinstance(payload, list) or not payload:
            break
        repos.extend(payload)
        if len(payload) < 100:
            break

    return repos


def _fetch_gitea_events(username: str, since: dt.datetime, until: dt.datetime) -> list[ActivityEvent]:
    base_url = os.getenv("GITEA_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
    headers = _gitea_headers()
    repos = _fetch_gitea_repos(username)
    events: list[ActivityEvent] = []
    seen_ids: set[str] = set()

    current_day = since.date()
    final_day = until.date()
    while current_day <= final_day:
        day_param = current_day.isoformat()
        for repo in repos:
            owner = ((repo.get("owner") or {}).get("login")) or username
            name = repo.get("name")
            if not name:
                continue

            for page in range(1, 6):
                try:
                    payload = _http_get_json(
                        f"{base_url}/api/v1/repos/{owner}/{name}/activities/feeds",
                        headers=headers,
                        params={"date": day_param, "limit": 50, "page": page},
                    )
                except HTTPError:
                    break
                if not isinstance(payload, list) or not payload:
                    break

                for item in payload:
                    act_user = item.get("act_user") or {}
                    act_login = (act_user.get("login") or act_user.get("username") or "").lower()
                    if act_login and act_login != username.lower():
                        continue

                    event_id = str(item.get("id") or "")
                    if event_id and event_id in seen_ids:
                        continue
                    if event_id:
                        seen_ids.add(event_id)

                    normalized = _normalize_gitea_event(item)
                    if normalized is None or normalized.created_at < since:
                        continue
                    events.append(normalized)

                if len(payload) < 50:
                    break
        current_day += dt.timedelta(days=1)

    return sorted(events, key=lambda item: item.created_at, reverse=True)


def _build_summary(platform: str, events: list[ActivityEvent], since: dt.datetime, until: dt.datetime) -> str:
    if not events:
        return (
            f"{platform}\n"
            f"Periodo: {since.strftime('%Y-%m-%d %H:%M')} ate {until.strftime('%Y-%m-%d %H:%M')} ({TIMEZONE})\n"
            "Nenhuma atividade encontrada."
        )

    type_counter = Counter(event.event_type for event in events)
    repo_counter = Counter(event.repo for event in events)

    lines = [
        platform,
        f"Periodo: {since.strftime('%Y-%m-%d %H:%M')} ate {until.strftime('%Y-%m-%d %H:%M')} ({TIMEZONE})",
        f"Total de eventos: {len(events)}",
        "Tipos mais frequentes: " + ", ".join(f"{name}={count}" for name, count in type_counter.most_common(5)),
        "Repositorios mais ativos: " + ", ".join(f"{name}={count}" for name, count in repo_counter.most_common(5)),
        "Atividades recentes:",
    ]
    for event in events[:8]:
        lines.append(
            f"- {event.created_at.strftime('%a %H:%M')} | {event.summary} | {event.details}"
        )
    return "\n".join(lines)


@mcp.tool()
async def dev_activity_help() -> str:
    return (
        "Ferramentas disponiveis:\n"
        "- summarize_weekly_dev_activity(platform='all'): resume sua atividade da semana atual em GitHub, Gitea ou ambos.\n"
        "Configuracao em api/.env:\n"
        "- GITHUB_USERNAME, GITHUB_TOKEN opcional, GITHUB_API_URL opcional\n"
        "- GITEA_USERNAME, GITEA_TOKEN opcional, GITEA_BASE_URL\n"
        "- MCP_EXTRA_ADDRESSES=http://localhost:8002/mcp para o backend consumir este MCP extra."
    )


@mcp.tool()
async def summarize_weekly_dev_activity(platform: str = "all") -> str:
    """Resume a atividade de desenvolvimento da semana atual no GitHub, no Gitea, ou em ambos."""
    platform_normalized = platform.strip().lower()
    if platform_normalized not in {"all", "github", "gitea"}:
        return "Parametro invalido. Use: all, github ou gitea."

    now = _now_local()
    since = _week_start(now)
    sections: list[str] = []

    if platform_normalized in {"all", "github"}:
        github_username = os.getenv("GITHUB_USERNAME", "VitorHolandaI")
        try:
            github_events = _fetch_github_events(github_username, since)
            sections.append(_build_summary("GitHub", github_events, since, now))
        except Exception as exc:
            sections.append(f"GitHub\nErro ao consultar atividade: {exc}")

    if platform_normalized in {"all", "gitea"}:
        gitea_username = os.getenv("GITEA_USERNAME", "vitor")
        try:
            gitea_events = _fetch_gitea_events(gitea_username, since, now)
            sections.append(_build_summary("Gitea", gitea_events, since, now))
        except Exception as exc:
            sections.append(f"Gitea\nErro ao consultar atividade: {exc}")

    return "\n\n".join(sections)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
