# Nextcloud appliance sync — moved (superseded)

The canonical spec for the T-Watch appliance's 7-day Nextcloud sync now lives in
the appliance (firmware) repo, restructured into a clean two-part form —
"what exists today" vs "the recommended contract":

> `factory/docs/mcp/nextcloud-appliance-sync.md`
> (repo: `/home/vitor/Arduino/factory`)

Keep the two in sync. The contract fields still map to this server's parsed
CalDAV data in `api/mcp_module/stremable_http/nextcloud_tasks.py` and the tools
in `api/mcp_module/stremable_http/stream2.py`.

Server-side work to implement the contract (add `sync_nextcloud_agenda` with
structured JSON, 7-day `calendar-query` with recurrence expansion, task
grouping, `complete`/error semantics, auth/TLS) is tracked in the checklist at
the end of the canonical spec.
