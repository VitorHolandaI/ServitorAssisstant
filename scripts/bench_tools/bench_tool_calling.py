"""Score small Ollama models on the agent's real tool surface.

docs/benchmark.md already measures throughput and reasoning. Neither says
whether a model picks the right tool, which is the only thing that matters
for an assistant whose every useful answer goes through one. Run:

    python scripts/bench_tools/bench_tool_calling.py qwen3.5:2b granite4:350m

Each model is measured alone: the others are evicted first, so it gets the
whole GPU, and the share of it that actually landed in VRAM is reported.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tool_schemas import extract_server_prompt, extract_tools  # noqa: E402

DEFAULT_HOST = "http://10.66.66.15:11434"
# All three MCP servers the agent connects to. Measuring only stream2 hid a
# third task family on :8003 and understated how crowded the choice is.
DEFAULT_SOURCES = (
    "api/mcp_module/stremable_http/stream2.py",
    "api/mcp_module/dev_activity/stream.py",
    "api/mcp_module/nextcloud_slim/stream.py",
)
SERVER_SOURCE = "api/server/Server.py"
REQUEST_TIMEOUT = 300

# A terse router instruction cut the 0.8b from 56 tokens to 18 and raised it
# from 7/14 to 10/14 — and took qwen3.5:2b from 11/14 down to 4/14, where it
# stopped emitting tool calls at all. It is per-model, never a global win.
ROUTER_PROMPT = (
    "Você é um roteador de ferramentas. Chame exatamente uma ferramenta. "
    "Não escreva texto, não explique, não cumprimente."
)

# What each vendor publishes for its own model. None means the vendor ships a
# chat template that already handles tools and documents no system prompt;
# adding one there measurably hurt (see ROUTER_PROMPT above).
VENDOR_PROMPTS: dict[str, str | None] = {
    # github.com/ibm-granite/granite-4.0-language-models — Prompt engineering
    # guide v2. Granite's template carries no default system prompt at all.
    "granite4": (
        "You are a helpful assistant with access to the following tools. "
        "You may call one or more tools to assist with the user query."
    ),
    # qwen.readthedocs.io/en/latest/framework/function_call.html — Hermes-style
    # tool use is already in the chat template; no system prompt documented.
    "qwen3.5": None,
    "qwen3": None,
    # ai.google.dev/gemma/docs/core/model_card_4 documents the system role and
    # the <|think|> token, but no tool-calling system prompt.
    "gemma4": None,
    "llama3.2": None,
    "nemotron-mini": None,
    "functiongemma": None,
}


def prompt_for(model: str) -> str | None:
    """The vendor's own system prompt for this model, matched by longest prefix."""
    name = model.lower()
    matches = [key for key in VENDOR_PROMPTS if name.startswith(key)]
    if not matches:
        raise KeyError(
            f"no documented system prompt for {model!r}; add it to VENDOR_PROMPTS. "
            f"Known prefixes: {sorted(VENDOR_PROMPTS)}"
        )
    return VENDOR_PROMPTS[max(matches, key=len)]

# Each vendor publishes the sampling settings its model was tuned for, and
# they disagree: Qwen warns against greedy decoding, IBM's Granite 4.0 asks
# for it. Scoring everything at temperature=0 measures none of them fairly.
SAMPLING: dict[str, dict[str, float | int]] = {
    # ai.google.dev/gemma/docs/core/model_card_4
    "gemma4": {"temperature": 1.0, "top_p": 0.95, "top_k": 64},
    # huggingface.co/Qwen/Qwen3.5-9B — Instruct (non-thinking), general tasks
    "qwen3.5": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0,
                "presence_penalty": 1.5, "repeat_penalty": 1.0},
    "qwen3": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0,
              "presence_penalty": 1.5, "repeat_penalty": 1.0},
    # ibm.com/granite/docs/models/granite4-2
    "granite4.2": {"temperature": 1.0, "top_p": 0.95},
    # ibm.com/granite/docs/models/granite — 4.0 asks for greedy decoding
    "granite4": {"temperature": 0.0, "top_p": 1.0, "top_k": 0},
    # ollama.com/library/llama3.2 publishes no sampling guidance
    "llama3.2": {"temperature": 0.0},
    "nemotron-mini": {"temperature": 0.0},
    "functiongemma": {"temperature": 0.0},
}


def sampling_for(model: str, greedy: bool) -> dict[str, float | int]:
    """The vendor's published settings, matched on the longest name prefix."""
    if greedy:
        return {"temperature": 0.0}
    name = model.lower()
    matches = [key for key in SAMPLING if name.startswith(key)]
    if not matches:
        raise KeyError(
            f"no published sampling settings for {model!r}; add it to SAMPLING "
            f"or pass --greedy. Known prefixes: {sorted(SAMPLING)}"
        )
    return dict(SAMPLING[max(matches, key=len)])


def loaded_models(host: str) -> list[str]:
    body = requests.get(f"{host}/api/ps", timeout=30).json()
    return [entry["name"] for entry in body.get("models", [])]


def unload(host: str, model: str) -> None:
    """Evict a model so the next one gets the whole GPU instead of the rest."""
    response = requests.post(f"{host}/api/generate",
                             json={"model": model, "keep_alive": 0}, timeout=120)
    response.raise_for_status()


def isolate(host: str, model: str) -> None:
    """Evict everything else. 4GB of VRAM holds exactly one of these."""
    for resident in loaded_models(host):
        if resident != model:
            unload(host, resident)


def gpu_fraction(host: str, model: str) -> float:
    """Share of the model's weights resident in VRAM, 0.0 to 1.0.

    Ollama offloads the overflow to the CPU without saying so, and a model
    running 7% on the GPU produces latency numbers that mean nothing.
    """
    body = requests.get(f"{host}/api/ps", timeout=30).json()
    for entry in body.get("models", []):
        if entry.get("name") != model:
            continue
        total = entry.get("size") or 0
        return (entry.get("size_vram") or 0) / total if total else 0.0
    return 0.0


@dataclass
class CaseResult:
    case_id: str
    called: str | None
    expected: list[str | None]
    tool_ok: bool
    args_ok: bool
    latency_ms: int
    eval_tokens: int
    prompt_eval_ms: int = 0
    eval_ms: int = 0
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.tool_ok and self.args_ok


@dataclass
class ModelResult:
    model: str
    options: dict[str, float | int] = field(default_factory=dict)
    gpu_share: float = 0.0
    cases: list[CaseResult] = field(default_factory=list)
    failure: str | None = None

    @property
    def score(self) -> float:
        if not self.cases:
            return 0.0
        return sum(1 for c in self.cases if c.passed) / len(self.cases)

    @property
    def median_latency_ms(self) -> int:
        return self._median("latency_ms")

    @property
    def median_eval_tokens(self) -> int:
        return self._median("eval_tokens")

    @property
    def median_prompt_eval_ms(self) -> int:
        return self._median("prompt_eval_ms")

    def _median(self, field_name: str) -> int:
        # The first case pays for loading the model from disk, so it is not
        # the steady state anyone will experience.
        warm = [c for c in self.cases[1:] if c.latency_ms > 0]
        values = [getattr(c, field_name) for c in warm]
        return int(statistics.median(values)) if values else 0


@dataclass
class Aggregate:
    """Several passes of the same config, because one pass cannot be ranked.

    A 14-case run swings by one or two cases between identical passes, so a
    single score cannot tell a better config from a luckier one.
    """

    model: str
    options: dict[str, float | int]
    runs: list[ModelResult] = field(default_factory=list)

    @property
    def passed_counts(self) -> list[int]:
        return [sum(1 for c in run.cases if c.passed) for run in self.runs]

    @property
    def total_cases(self) -> int:
        return len(self.runs[0].cases) if self.runs else 0

    @property
    def mean_passed(self) -> float:
        counts = self.passed_counts
        return sum(counts) / len(counts) if counts else 0.0

    @property
    def median_latency_ms(self) -> int:
        values = [c.latency_ms for run in self.runs
                  for c in run.cases[1:] if c.latency_ms > 0]
        return int(statistics.median(values)) if values else 0

    def case_outcomes(self) -> dict[str, int]:
        """How many passes each case survived, across every run."""
        tally: dict[str, int] = {}
        for run in self.runs:
            for case in run.cases:
                tally[case.case_id] = tally.get(case.case_id, 0) + int(case.passed)
        return tally

    @property
    def always_failed(self) -> list[str]:
        return sorted(k for k, v in self.case_outcomes().items() if v == 0)

    @property
    def flaky(self) -> list[str]:
        runs = len(self.runs)
        return sorted(k for k, v in self.case_outcomes().items() if 0 < v < runs)


def load_cases(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"cases file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{path} has no 'cases' list")
    return cases


def load_tools(sources: tuple[str, ...],
               drop: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Every @mcp.tool(), minus the names matching a substring in `drop`.

    Prompt eval is 708ms of gemma4's 2882ms median, so the length of this
    list is a latency knob and not only an accuracy one.
    """
    tools: list[dict[str, Any]] = []
    for relative in sources:
        tools.extend(extract_tools(ROOT / relative))
    if not drop:
        return tools
    kept = [
        tool for tool in tools
        if not any(pattern in tool["function"]["name"] for pattern in drop)
    ]
    if len(kept) == len(tools):
        raise ValueError(f"--drop {drop} matched no tool out of {len(tools)}")
    return kept


def ask(host: str, model: str, prompt: str, tools: list[dict[str, Any]],
        num_ctx: int, think: bool | None, options: dict[str, float | int],
        system: str | None) -> tuple[dict[str, Any], int]:
    """One /api/chat turn. Returns the body plus wall-clock milliseconds."""
    messages = [{"role": "user", "content": prompt}]
    if system:
        messages.insert(0, {"role": "system", "content": system})
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "stream": False,
        "options": {"num_ctx": num_ctx, **options},
        # -1 pins the model in VRAM, which is what production wants and what
        # keeps the first case from being the only one that pays for loading.
        "keep_alive": -1,
    }
    if think is not None:
        payload["think"] = think
    started = time.monotonic()
    response = requests.post(f"{host}/api/chat", json=payload, timeout=REQUEST_TIMEOUT)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    response.raise_for_status()
    body = response.json()
    if body.get("error"):
        raise RuntimeError(f"ollama refused {model!r}: {body['error']}")
    return body, elapsed_ms


def first_tool_call(message: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    calls = message.get("tool_calls") or []
    if not calls:
        return None, {}
    function = calls[0].get("function") or {}
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    return function.get("name"), arguments or {}


def check_arguments(case: dict[str, Any], arguments: dict[str, Any]) -> bool:
    """Required keys must be present and non-empty; absent keys must be missing."""
    for key in case.get("args_required", []):
        value = arguments.get(key)
        if value is None or value == "":
            return False
    for key in case.get("args_absent", []):
        if key in arguments:
            return False
    for key, expected in (case.get("args_contain") or {}).items():
        if str(arguments.get(key, "")).strip().lower() != str(expected).strip().lower():
            return False
    return True


def run_case(host: str, model: str, case: dict[str, Any], tools: list[dict[str, Any]],
             num_ctx: int, think: bool | None, options: dict[str, float | int],
             system: str | None) -> CaseResult:
    expected = case["accept"]
    try:
        body, elapsed_ms = ask(
            host, model, case["prompt"], tools, num_ctx, think, options, system
        )
    except (requests.RequestException, RuntimeError, ValueError) as error:
        return CaseResult(case["id"], None, expected, False, False, 0, 0, error=str(error))
    called, arguments = first_tool_call(body.get("message") or {})
    tool_ok = called in expected
    return CaseResult(
        case_id=case["id"],
        called=called,
        expected=expected,
        tool_ok=tool_ok,
        args_ok=tool_ok and check_arguments(case, arguments),
        latency_ms=elapsed_ms,
        eval_tokens=int(body.get("eval_count") or 0),
        prompt_eval_ms=int((body.get("prompt_eval_duration") or 0) / 1e6),
        eval_ms=int((body.get("eval_duration") or 0) / 1e6),
    )


def run_model(host: str, model: str, cases: list[dict[str, Any]],
              tools: list[dict[str, Any]], num_ctx: int, think: bool | None,
              options: dict[str, float | int], system: str | None) -> ModelResult:
    isolate(host, model)
    result = ModelResult(model=model, options=options)
    for case in cases:
        case_result = run_case(
            host, model, case, tools, num_ctx, think, options, system
        )
        result.cases.append(case_result)
        mark = "ok  " if case_result.passed else "FAIL"
        detail = case_result.error or f"called={case_result.called}"
        print(f"  [{mark}] {case_result.case_id:<24} {case_result.latency_ms:>6}ms"
              f"  {case_result.eval_tokens:>3}tok  {detail}")
        if case_result.error and "refused" in case_result.error:
            result.failure = case_result.error
            return result
        if result.gpu_share == 0.0:
            result.gpu_share = gpu_fraction(host, model)
    return result


def print_report(aggregates: list[Aggregate], tool_count: int, num_ctx: int) -> None:
    runs = len(aggregates[0].runs) if aggregates else 0
    print(f"\n## Tool calling — {tool_count} tools, num_ctx={num_ctx}, {runs}x\n")
    print("| Modelo | Acerto médio | Faixa | Latência mediana |")
    print("|---|---|---|---|")
    for item in sorted(aggregates, key=lambda a: a.mean_passed, reverse=True):
        counts = item.passed_counts
        if not counts:
            print(f"| {item.model} | indisponível | — | — |")
            continue
        spread = (f"{min(counts)}–{max(counts)}" if min(counts) != max(counts)
                  else str(counts[0]))
        print(f"| {item.model} | {item.mean_passed:.1f}/{item.total_cases}"
              f" | {spread} | {item.median_latency_ms} ms |")
    for item in aggregates:
        shares = [run.gpu_share for run in item.runs if run.gpu_share]
        if shares and min(shares) < 0.99:
            print(f"\n> AVISO: {item.model} rodou {min(shares):.0%} na GPU."
                  " As latências dele não são comparáveis.")
    print("\n### Estabilidade por caso\n")
    for item in aggregates:
        print(f"**{item.model}** — {item.passed_counts}")
        if item.always_failed:
            print(f"- falha sempre: {', '.join(item.always_failed)}")
        if item.flaky:
            print(f"- instável: {', '.join(item.flaky)}")
        if not item.always_failed and not item.flaky:
            print("- passou tudo em todas as execuções")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+", help="Ollama model tags to score")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--think", choices=("false", "true", "default"), default="false")
    parser.add_argument("--greedy", action="store_true",
                        help="temperature=0 for every model instead of the vendor's settings")
    parser.add_argument("--cases", type=Path,
                        default=Path(__file__).resolve().parent / "cases.json")
    parser.add_argument("--router-prompt", action="store_true",
                        help="prepend the terse router system prompt to every model")
    parser.add_argument("--vendor-prompt", action="store_true",
                        help="use each vendor's own documented system prompt")
    parser.add_argument("--server-prompt", action="store_true",
                        help="use the system prompt Server.py actually sends")
    parser.add_argument("--system-file", type=Path,
                        help="read the system prompt from this file")
    parser.add_argument("--drop", default="",
                        help="comma-separated substrings; tools whose name "
                             "contains one are hidden from the model")
    parser.add_argument("--repeat", type=int, default=1,
                        help="passes per model; >1 reports mean and spread")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    think = {"false": False, "true": True, "default": None}[args.think]
    cases = load_cases(args.cases)
    drop = tuple(part.strip() for part in args.drop.split(",") if part.strip())
    tools = load_tools(DEFAULT_SOURCES, drop)
    chosen = sum((args.router_prompt, args.vendor_prompt, args.server_prompt,
                  bool(args.system_file)))
    if chosen > 1:
        parser.error("--router-prompt, --vendor-prompt, --server-prompt e "
                     "--system-file são mutuamente exclusivos")
    print(f"{len(tools)} tools, {len(cases)} casos, think={args.think}, "
          f"router_prompt={args.router_prompt}, vendor_prompt={args.vendor_prompt}, "
          f"server_prompt={args.server_prompt}, greedy={args.greedy}, "
          f"drop={drop or '-'}, host={args.host}")

    aggregates: list[Aggregate] = []
    for model in args.models:
        options = sampling_for(model, args.greedy)
        system = ROUTER_PROMPT if args.router_prompt else None
        if args.vendor_prompt:
            system = prompt_for(model)
        if args.server_prompt:
            system = extract_server_prompt(ROOT / SERVER_SOURCE)
        if args.system_file:
            system = args.system_file.read_text(encoding="utf-8").strip()
        print(f"\n== {model}  {options}  system={'sim' if system else 'nenhum'}")
        aggregate = Aggregate(model=model, options=options)
        for attempt in range(args.repeat):
            if args.repeat > 1:
                print(f"  -- passe {attempt + 1}/{args.repeat}")
            aggregate.runs.append(
                run_model(args.host, model, cases, tools, args.num_ctx, think,
                          options, system)
            )
        aggregates.append(aggregate)
    print_report(aggregates, len(tools), args.num_ctx)

    if args.json_out:
        payload = [
            {"model": a.model, "options": a.options,
             "passed_counts": a.passed_counts, "total": a.total_cases,
             "always_failed": a.always_failed, "flaky": a.flaky,
             "runs": [{"gpu_share": r.gpu_share, "failure": r.failure,
                       "cases": [vars(c) for c in r.cases]} for r in a.runs]}
            for a in aggregates
        ]
        args.json_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"JSON em {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
