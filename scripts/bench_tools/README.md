# scripts/bench_tools — medir acerto de tool calling

## Papel no sistema

Toda resposta útil do Servitor passa por uma tool: clima, agenda, tarefa,
energia, Wake-on-LAN. O modelo pode ser rápido e lúcido e ainda assim
inútil se escolher a tool errada. O `docs/benchmark.md` mede throughput
(tok/s por `num_ctx`) e raciocínio (3 perguntas), e é com base nele que o
modelo atual foi escolhido — mas nenhum dos dois eixos olha para tool
calling. Esta pasta preenche esse buraco.

Ninguém em produção depende destes arquivos. Eles são ferramenta de
decisão: rodar antes de trocar `OLLAMA_MODEL` em `api/server/Server.py:46`.

## Arquivos

`tool_schemas.py` — extrai, via `ast`, as funções decoradas com
`@mcp.tool()` dos **três** servidores MCP (`stream2.py` :8001,
`dev_activity/stream.py` :8002, `nextcloud_slim/stream.py` :8003) e devolve
a lista no formato que o `/api/chat` do Ollama espera. São 36 tools,
conferidos contra o `list_tools` dos servidores vivos. Honra o nome que o
decorator registra: `@mcp.tool('create_local_task')` vale mais que o nome
da função. Não importa nada do projeto, então roda sem subir o stack e sem
as dependências do servidor. Existe para que o benchmark veja a mesma
superfície que o agente vê: um JSON copiado à mão envelhece em silêncio e
passa a medir tools que não existem mais.

`prompts/` — candidatos a system prompt, um por arquivo, usados com
`--system-file`. Ficam em arquivo e não em string no código para dar para
iterar sem tocar no runner e para a versão ficar no git.

`cases.json` — os casos, em português falado, do jeito que o assistente é
usado de verdade. Cada caso traz `accept` (as tools que contam como
acerto; `null` quer dizer "não chame nada"), e opcionalmente
`args_required`, `args_absent` e `args_contain`. O campo `why` diz o que
cada caso está testando — apague um caso só sabendo o que você deixa de
medir.

`bench_tool_calling.py` — o runner e ponto de entrada. Consome os dois
acima, manda um turno por caso para o `/api/chat`, lê o primeiro
`tool_calls` da resposta e pontua seleção e argumentos separadamente.
Imprime tabela markdown pronta para colar no `docs/benchmark.md` e aceita
`--json-out` para comparar execuções.

Carrega também dois mapas de configuração por família, cada um com a URL
da fonte em comentário: `SAMPLING` (temperature/top_p/top_k publicados
pelo fabricante) e `VENDOR_PROMPTS` (o system prompt que o fabricante
documenta, ou `None` quando ele não documenta nenhum). Modelo sem entrada
nos dois mapas levanta `KeyError` em vez de ser medido com um default
inventado.

## Fluxo

`bench_tool_calling.py` → `tool_schemas.extract_tools()` (schemas) +
`cases.json` (prompts) → para cada modelo, `isolate()` descarrega os
outros → um POST por par (modelo, caso) para o Ollama → `CaseResult` →
`ModelResult` → tabela.

`isolate()` existe porque nos 4GB cabe um modelo só. Rodando em sequência
sem descarregar, o Ollama manda o excedente para a CPU em silêncio — o
`granite4:350m` chegou a rodar com 7% dos pesos em VRAM, e a latência
medida ali era ficção. Por isso o relatório traz a coluna "Na GPU"
(`size_vram / size` do `/api/ps`) e avisa quando ela fica abaixo de 99%.

## Uso

```bash
python scripts/bench_tools/bench_tool_calling.py qwen3:1.7b llama3.2:3b
```

Flags: `--host` (padrão `http://10.66.66.15:11434`), `--num-ctx` (padrão
8192), `--think false|true|default`, `--greedy`, `--repeat N`, `--drop`,
`--cases`, `--json-out`, e um dos quatro de system prompt abaixo.

`--greedy` força `temperature=0` em todos em vez do parâmetro oficial de
cada um; é assim que se mede quanto a recomendação do fabricante muda o
resultado. Em greedy o modelo é determinístico, então comparar duas configs
assim é sinal e não sorte.

`--repeat N` roda N passes do mesmo config e reporta média, faixa, e as
duas listas que importam: **falha sempre** (limitação) e **instável**
(sorte). Em greedy a faixa vem zerada; é com a temperatura do fabricante
que ela abre.

`--drop a,b` esconde do modelo os tools cujo nome contém `a` ou `b`. Serve
para medir um perfil antes de criá-lo em `api/mcp_module/profiles.py`:
`--drop _local_task,help` tira os 8 que mais atrapalharam.

Os quatro modos de system prompt são mutuamente exclusivos, e sem nenhum
deles nada é enviado:

| Flag | O que manda |
|---|---|
| `--router-prompt` | o mesmo prompt curto de roteamento em todo mundo |
| `--vendor-prompt` | o prompt que cada fabricante documenta |
| `--server-prompt` | o que o `Server.py` manda de verdade, lido via AST |
| `--system-file X` | o conteúdo de um arquivo de `prompts/` |

`--server-prompt` é o que torna a medição honesta: medir com prompt
inventado mede um assistente que ninguém roda. O de produção tem 810
palavras e custa 4 casos.

O padrão é `--think false`. Com `default`, o Ollama usa o comportamento do
modelo, que nos modelos "thinking" gasta a geração inteira no raciocínio
interno — é o mesmo efeito registrado nas notas do `docs/benchmark.md`.

## Limites

- Mede o **primeiro** `tool_calls` de **um** turno. Não mede cadeia de
  várias tools, nem recuperação depois de uma tool falhar.
- `accept` é uma lista de nomes, então ele não julga se a tool escolhida
  era a *melhor* entre duas defensáveis — só se estava na lista.
- A VRAM é 4GB (Quadro M2000M). Modelo que não couber vai para a CPU; a
  coluna "Na GPU" denuncia, mas não conserta.
- System prompt é config por modelo. O mesmo prompt curto vale +3 casos
  para o `qwen3.5:0.8b` e −7 para o `qwen3.5:2b`. Não existe um default
  que sirva para todos, e é por isso que o runner não manda nenhum a menos
  que você peça.
- Mede escolha de ferramenta, nunca a qualidade do texto. O prompt que
  venceu com 13/14 manda o modelo não escrever texto, o que é ótimo aqui e
  ruim num assistente que depois fala a resposta. Um ganho nesta pasta não
  é automaticamente um ganho em produção.
