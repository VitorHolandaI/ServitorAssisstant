# Nextcloud appliance sync — moved (superseded)

The canonical spec for the T-Watch appliance's 7-day Nextcloud sync now lives in
the appliance (firmware) repo, restructured into a clean two-part form —
"what exists today" vs "the recommended contract":

> `factory/docs/mcp/nextcloud-appliance-sync.md`
> (repo: `/home/vitor/tinker_git/leWatch`, github.com/VitorHolandaI/leWatch)

O caminho `/home/vitor/Arduino/factory` é uma cópia solta, fora do git e
parada em 10/09. Não edite por lá.

Keep the two in sync. The contract fields still map to this server's parsed
CalDAV data in `api/mcp_module/stremable_http/nextcloud_tasks.py` and the tools
in `api/mcp_module/stremable_http/stream2.py`.

O trabalho do servidor está feito: `sync_nextcloud_agenda` existe em
`stream2.py` com JSON estruturado, janela de N dias, expansão de recorrência e
agrupamento de tarefas. Verificado ao vivo em 29/10/2026: 107 eventos e 41
tarefas em 7 dias, `errors: []`. O relógio chama com
`{"days": 7, "all_tasks": true}` — o `all_tasks` não está na §2.1 original da
spec e é o que faz as tarefas sem data chegarem lá.

Falta auth e TLS no endpoint MCP. O checklist no fim da spec canônica é a
contagem autoritativa.
