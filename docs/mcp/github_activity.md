# Weekly Dev Activity

Tool exposed by the weekly activity MCP server (`api/mcp_module/dev_activity/stream.py`).

## Tool

`summarize_weekly_dev_activity(platform?)`

- `platform`: optional, one of `all`, `github`, `gitea`
- default: `all`

## What it does

Builds a summary of your activity in the current week, from Monday 00:00 until now, using:

- GitHub user events for `GITHUB_USERNAME`
- Gitea repository activity feeds for `GITEA_USERNAME`

## Required config

Set these in `api/.env`:

```env
MCP_EXTRA_ADDRESSES=http://localhost:8002/mcp

GITHUB_USERNAME=VitorHolandaI
GITHUB_TOKEN=

GITEA_BASE_URL=http://10.66.66.11:3000
GITEA_USERNAME=vitor
GITEA_TOKEN=
```

`GITHUB_TOKEN` and `GITEA_TOKEN` are optional, but recommended if you want private activity included.

## Example prompts

- `resume minha atividade da semana`
- `mostre so a atividade do github desta semana`
- `quero um resumo do gitea ate agora`
