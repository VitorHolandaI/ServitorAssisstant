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