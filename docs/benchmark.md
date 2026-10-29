# Benchmark de modelos Ollama no hardware atual

**GPU:** Quadro M2000M (4GB VRAM)
**RAM:** 32GB (sistema)
**Ollama:** 0.32.1
**Teste:** Contexto progressivo com `num_ctx` forçado

---

## Degradação de desempenho por contexto

Cada célula mostra `prompt_eval (tok/s) / geração (tok/s)`.

| Modelo | Tamanho | 2K ctx | 8K ctx | 16K ctx | 32K ctx |
|---|---|---|---|---|---|
| **qwen3.5:2b** (think=false) | 2.6GB | **295 / 19** | **283 / 18** | **255 / 16** | **255 / 15** |
| gemma4:e2b-it-qat (think=false) | 4.0GB | 186 / 17 | 163 / 16 | 129 / 15 | 129 / 15 |
| qwen2.5:3b | 1.8GB | 161 / 11 | 152 / 10 | 112 / 8 | 105 / 8 |
| llama3.2:3b | 2.0GB | 153 / 11 | 119 / 10 | — | 119 / 9 |
| granite3.2:2b | 1.5GB | 168 / 13 | 121 / 11 | — | 114 / 11 |
| nemotron-mini | 2.7GB | 156 / 10 | 144 / 10 | — | 144 / 10 |
| phi4-mini | 2.5GB | 133 / 9 | 107 / 8 | — | 104 / 6 |

Observação: `prompt_eval tok/s` cai com o contexto porque o KV cache cresce. Geração (`tok/s`) degrada bem menos (~20% de 2K para 32K).

## Raciocínio (3 perguntas)

| Modelo | Acertos |
|---|---|
| **qwen3.5:2b** (think=false) | 2/3 (errou a trick das 17 ovelhas) |
| gemma4:e2b-it-qat (think=false) | **3/3** (acertou todas) |
| qwen2.5:3b | 1/3 (só acertou a mais fácil) |
| qwen3.5:4b (think=false) | **3/3** (acertou todas) |
| llama3.2:3b | 0/3 |
| granite3.2:2b | 0/3 |
| nemotron-mini | 1/3 |
| phi4-mini | 0/3 |

## Configuração atual

Modelo escolhido: **qwen3.5:2b**
- `num_ctx`: 32768
- `think`: false
- `timeout`: 120s (LLM), 300s (producer)
- `keep_alive`: -1

## Notas

- Modelos "thinking" (qwen3.5, gemma4) precisam de `think=false` senão gastam todos os tokens no raciocínio interno e devolvem `response` vazio. Bug conhecido do Ollama.
- `num_ctx` padrão do Ollama é ~2048. Sem forçar, o prompt é truncado.
- Contexto de 32K tokens cabe sem OOM. Para mais que isso, o prompt eval fica inviável (>2min).
- Tok/s de geração é estável (~15 t/s) mesmo com contexto grande.
---

## Acerto de tool calling (2026-10-02)

Throughput e raciocínio não dizem se o modelo escolhe a tool certa, que é
por onde passa toda resposta útil do assistente. Harness, casos e critério: `scripts/bench_tools/` (os schemas saem dos
três servidores MCP via AST — 36 tools, conferidos contra o `list_tools`
dos servidores vivos).

Correção de método: as medições com 30 tools, mais abaixo, leram só o
`stream2.py`. Faltavam os 2 do `:8002` e os 4 do `:8003`, e esses últimos
são uma terceira família de tarefa — então aquelas rodadas **subestimam**
a lotação da escolha.

14 casos em português falado, `num_ctx=8192`, `think=false`,
`keep_alive=-1`, GPU do `.15`, **um modelo por vez** com os outros
descarregados antes.

### Melhor configuração de cada modelo

| Modelo | Config | Acerto | Latência mediana | Prompt eval |
|---|---|---|---|---|
| **gemma4:e2b-it-qat** | oficial + system prompt curto | **11/14 (79%)** | **2882 ms** | 708 ms |
| qwen3.5:2b | oficial, sem system prompt | 11/14 (79%) | 4210 ms | 1958 ms |
| qwen3.5:0.8b | oficial + system prompt curto | 10/14 (71%) | 4456 ms | 2011 ms |
| granite4:350m | greedy + prompt da IBM | 8/14 (57%) | 1743 ms | 107 ms |
| nemotron-mini | qualquer | 0/10 | — | — |

### Medir isolado não é detalhe

Rodando os modelos em sequência sem descarregar o anterior, o Ollama
manda o excedente para a CPU **sem avisar**: o `qwen3.5:0.8b` ficou com
22% dos pesos em VRAM e o `granite4:350m` com 7%. As latências dessa
rodada eram ficção. O runner agora descarrega os outros antes de cada
modelo e imprime `size_vram / size` do `/api/ps`; abaixo de 99% ele emite
um aviso de que a medição não vale.

### System prompt é config por modelo, não global

Mesmos parâmetros, mesma lista de casos, só o system prompt mudando:

| Modelo | Com system prompt curto | Sem |
|---|---|---|
| qwen3.5:2b | 4/14 (29%) | **11/14 (79%)** |
| qwen3.5:0.8b | **10/14 (71%)** | 7/14 (50%) |

Opostos. No `qwen3.5:2b` o prompt faz o modelo parar de emitir tool call:
10 dos 14 casos voltam `None`, com `content` vazio e 13–18 tokens que não
viram nada. Confere com o que a Qwen documenta — o formato Hermes já está
no chat template e nenhum system prompt é recomendado
(qwen.readthedocs.io/en/latest/framework/function_call.html).

O Granite é o oposto: a IBM publica o prompt dele, e usá-lo levou o
`granite4:350m` de 4/14 para 8/14.

### Parâmetros oficiais, por fabricante

| Família | temperature | top_p | top_k | Fonte |
|---|---|---|---|---|
| gemma4 | 1.0 | 0.95 | 64 | ai.google.dev/gemma/docs/core/model_card_4 |
| qwen3.5 / qwen3 | 0.7 | 0.8 | 20 | huggingface.co/Qwen/Qwen3.5-9B |
| granite4 (4.0) | 0.0 | 1.0 | 0 | ibm.com/granite/docs/models/granite |
| granite4.2 | 1.0 | 0.95 | — | ibm.com/granite/docs/models/granite4-2 |

Ressalva no Gemma: com o `temperature=1.0` do Google ele divagou em vez de
chamar a tool em três casos (117–129 tokens, ~10s cada) e num deles gastou
1785 tokens e 133 segundos. Com `temperature=0` fez os mesmos 11/14, com
mediana de 3102 ms e nenhum outlier. Para voz, o greedy off-spec é a
escolha mais segura.

### A capability `tools` do registry não é evidência

`nemotron-mini` anuncia `tools` no `/api/show` e não emite tool call: zero
em 10 casos, e um controle com um único tool trivial devolveu
`tool_calls: null` com `content: " "`. Confirme sempre com um caso real.

### O gargalo não é o modelo

As escritas de tarefa falham em todos os modelos. `create_nextcloud_task`
tem 5 argumentos e exige `due_at` em `'YYYY-MM-DD HH:MM:SS'` exato;
`complete_nextcloud_task` e `delete_nextcloud_task` pedem título exato ou
UID. O modelo pequeno desiste (`None`) ou foge para a família SQLite.

E essa família é armadilha ativa: os 6 `*_local_task` se defendem só com a
frase "ONLY use this when the user explicitly asks for local or SQLite"
dentro da descrição, e todos os modelos caíram nela. O perfil `local` em
`api/mcp_module/profiles.py` já os exclui; o `full`, que é o do servidor,
não.

Prompt eval responde se vale cortar a lista: 708 ms no gemma4 e ~2000 ms
no qwen3.5, contra 107 ms no granite. É um quarto a metade da latência
total. Cortar tools reduz tempo **e** melhora acerto.

### O system prompt pesa mais que o modelo

Mesmo modelo, mesmo hardware, mesmas 14 provas, `temperature=0`. Só o
system prompt muda:

| System prompt | Palavras | Acerto |
|---|---|---|
| `Server.py` atual — persona + bloco CRITICAL + dicas de roteamento | 810 | 9/14 (64%) |
| persona de magos em 3 linhas, PT (`prompts/curto_pt.md`) | 33 | 10/14 (71%) |
| só a regra de usar ferramenta, PT (`prompts/sem_persona.md`) | 24 | 12/14 (86%) |
| roteador de ferramentas, PT, com 22 tools | 21 | **13/14 (93%)** |

As 5 falhas do prompt de produção são **todas** `called=None`: o modelo
para de chamar tool. Inclusive `tarefas_listar`, que é justamente o caso
para o qual esse prompt tem instrução escrita à mão dizendo "ALWAYS call
list_nextcloud_tasks() first". Numa janela de 2B, 810 palavras de ordem em
inglês afogam a decisão que elas tentam dirigir.

Isso não é copiável direto. O prompt de 13/14 manda "não escreva texto",
ótimo para escolher ferramenta e ruim para um assistente que depois fala a
resposta. Os dois usos têm ótimos diferentes e só um foi medido. Separar
em duas chamadas — roteamento com prompt curto, redação com a persona — é
o que fecha os dois, ao custo de mexer no `client2.py`.

### Greedy é determinístico; o resto não

`temperature=0` repetido 3x deu `[13, 13, 13]` e `[12, 12, 12]`, sem um
único caso instável, e duas execuções com `num_ctx` diferente produziram
saída idêntica token a token. Então comparação entre configs em greedy é
sinal, não sorte.

As rodadas com o `temperature=1.0` oficial do Gemma é que variam, e variam
feio: 1785 tokens / 133 s num caso, três de ~10 s noutra rodada. Para voz,
o greedy off-spec é a escolha segura.

### O que produção manda hoje

`ChatOllama` só envia os campos preenchidos, então o payload real é
`options: {'num_ctx': 32768}` — **sem temperatura**, o que deixa o Ollama
aplicar o default 0.8. Ou seja, o servidor roda não-determinístico, na
faixa dos outliers.

| | Hoje | Melhor medido |
|---|---|---|
| system prompt | 810 palavras | 3 linhas |
| tools | 30 | 22 |
| temperature | 0.8 implícito | 0 |
| keep_alive | `10m` | `-1` |
| num_ctx | 32768 | indiferente |
| acerto | 9/14 (64%) | 13/14 (93%) |

### Alavancas que não funcionaram

- **`num_ctx`** 32768 → 8192: nada. 11/14 nos dois, 2950 ms contra
  2988 ms, prompt eval 699 ms contra 700 ms. Vale por VRAM, não por tempo.
- **Cortar tools não reduz prompt eval.** 30 → 22 tools manteve o prompt
  eval em ~700 ms. O ganho de latência veio dos tokens gerados, não do
  prompt. E o efeito no acerto depende do prompt: +1 com o de roteamento,
  −1 com o sem persona.

### A combinação que fecha: slim-first + prompt curto

A superfície real tem **três** famílias de tarefa competindo: 11 tools
`*_nextcloud_*` no `stream2`, 6 `*_local_task` em SQLite, e 4 `nextcloud_*`
no servidor slim do `:8003`.

As do slim são as únicas que um modelo de 2B consegue usar:

| Pesada (`stream2`) | Slim (`:8003`) |
|---|---|
| `create_nextcloud_task` — 5 args, `due_at` em `'YYYY-MM-DD HH:MM:SS'` | `nextcloud_add_task(title, due_at)` — `due_at` em linguagem natural |
| `complete_nextcloud_task` — título exato ou UID, + calendar | `nextcloud_finish_task(task)` — título ou id curto |
| `list_nextcloud_tasks` — 3 args | `nextcloud_pending_tasks(limit)` |
| `list_nextcloud_events` — 2 args | `nextcloud_today()` — sem argumento |

Três cenários, `temperature=0`, mesma máquina:

| Cenário | Tools | System prompt | Acerto | Latência |
|---|---|---|---|---|
| A — produção como está | 36 | 810 palavras | 8/14 (57%) | 2378 ms |
| B — perfil slim-first | 18 | 810 palavras | 8/14 (57%) | 2135 ms |
| **C — slim-first + prompt curto** | **18** | **21 palavras** | **14/14 (100%)** | 2406 ms |

**A ordem importa, e é contraintuitiva.** Arrumar a superfície de tools
sozinho não muda nada: A e B empatam em 8/14. O que muda no B é só o
comportamento interno — em `tarefa_criar` o modelo passa a escolher
`nextcloud_add_task` em vez da pesada — mas o placar não move, porque as 6
falhas são todas `called=None`. O prompt bloqueia antes de a escolha
acontecer.

Então: o prompt é condição necessária, e a superfície slim é o que destrava
as operações de tarefa **depois** que o prompt deixa o modelo chamar tool.
Mexer só no `profiles.py` é trabalho perdido.

No C passam `tarefa_concluir` e `tarefa_apagar`, que falhavam em toda
config, com todo modelo, em toda a sessão.

Ressalva de critério: no mesmo passo em que os três servidores MCP
entraram, os casos de tarefa passaram a aceitar a tool do slim como
correta. Num perfil slim-first ela é a resposta certa, mas o 14/14 é
contra critério ajustado.

### Pendente