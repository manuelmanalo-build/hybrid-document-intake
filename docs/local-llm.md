# Running with a local, open-weight model

`AGENT_PROVIDER=local` runs the pipeline and the live evaluations against an open-weight model on
your own machine or server, through any OpenAI-compatible endpoint: [Ollama](https://ollama.com)
(the default), LM Studio, vLLM, or the llama.cpp server.

Why you might want this:

- **Documents never leave your machine.** No packet text, image, or extracted value is sent to a
  cloud provider. For the CDP pack, personal identifiers are still redacted before the model sees
  anything.
- **No per-call cost.** The live evaluations need no cost approval when the provider is local.
- **A fair comparison.** The same evaluations and gates run unchanged, so a local model's numbers
  sit next to a cloud model's.

What to expect: local models are slower, and smaller ones are less accurate, especially at the
agent's tool selection and at long structured outputs with per-field evidence. The rules, queues,
and human approval are unchanged, so a weaker model produces more items for review, not unsafe
approvals. The evaluations' gates (no false "ready" routes) tell you whether a model is good enough.

## What the model must support

| Capability | Why | Notes |
|---|---|---|
| **Tool calling** | The agent chooses each step by calling exactly one tool | On ollama.com, filter models by the *tools* tag |
| **Structured outputs** | Extraction and page labeling return JSON matching a schema | Ollama, vLLM, LM Studio, and llama.cpp support `response_format` with a JSON schema |
| **Vision** (optional) | Only the scanned packets (2 per evaluation set) have no text layer | Without it, those packets fail and are counted as errors; digital packets are unaffected |
| **Context window of 32k tokens or more** | Prompts with the output schema and a packet run 5–10k tokens, plus up to 8k of output | The most common cause of garbled results: see step 2 |

A 14-billion-parameter model with tool support is a reasonable starting point on a machine with
16 GB or more of memory (32 GB is comfortable). For example, `qwen3:14b` supports tools (text only);
the Gemma 4 family adds vision. Model names and tags change often: check
[ollama.com/search?c=tools](https://ollama.com/search?c=tools) for the current list.

## Setup with Ollama

1. **Install Ollama** from [ollama.com](https://ollama.com) and pull a model:

   ```bash
   ollama pull qwen3:14b
   ```

2. **Raise the context window.** Ollama's default depends on GPU memory and is only 4k tokens below
   24 GB, which silently truncates these prompts. Either move the **context length** slider in the
   Ollama app's settings to 32k or more, or start the server with:

   ```bash
   OLLAMA_CONTEXT_LENGTH=32768 ollama serve
   ```

   The OpenAI-compatible API cannot set the context size per request, so it has to be set on the
   server. Run `ollama ps` after the first request to confirm the model is not offloaded to the CPU.

3. **Point the platform at it.** In `.env`:

   ```bash
   AGENT_PROVIDER=local
   AGENT_MODEL=qwen3:14b
   AGENT_BASE_URL=http://host.docker.internal:11434/v1
   # Local models are slower: extraction runs inside a tool call, so raise all three limits.
   AGENT_MODEL_TIMEOUT_SECONDS=300
   AGENT_TOOL_TIMEOUT_SECONDS=300
   AGENT_TOTAL_TIMEOUT_SECONDS=900
   ```

   `host.docker.internal` lets the orchestrator container reach Ollama on your machine. It works out
   of the box on Docker Desktop (macOS and Windows). **On Linux**, Ollama listens only on
   `127.0.0.1` by default, which containers cannot reach: set `OLLAMA_HOST=0.0.0.0:11434` for the
   Ollama service (see Ollama's FAQ), and keep port 11434 closed to other machines with your
   firewall.

4. **Rebuild and run** as usual (`docker compose --profile local --env-file .env up --build --wait`),
   then upload a packet. Processing a packet takes minutes rather than seconds on a laptop.

## Running the evaluations locally

The evaluations run in your terminal, not in Docker, so use `localhost`:

```bash
export AGENT_PROVIDER=local AGENT_MODEL=qwen3:14b AGENT_BASE_URL=http://localhost:11434/v1
export AGENT_MODEL_TIMEOUT_SECONDS=300
export PYTHONPATH=.:services/api/src:services/orchestrator/src
uv run python -m evaluation.live_briefs_extraction
```

No `EVALUATION_LIVE_ENABLED` or API key is needed. The report records the provider (`local`) and
the model name, so local and cloud runs can be compared side by side. With a text-only model, the
two scanned packets in each set are reported as errors.

## Other servers

| Server | `AGENT_BASE_URL` (from your terminal) | Notes |
|---|---|---|
| Ollama | `http://localhost:11434/v1` | Default |
| LM Studio | `http://localhost:1234/v1` | Start the local server in LM Studio and load a model with tool use |
| vLLM | `http://localhost:8000/v1` | Start with tool calling enabled for your model; set `AGENT_API_KEY` if you started it with `--api-key` |
| llama.cpp server | `http://localhost:8080/v1` | Start with `--jinja` so tool-calling chat templates apply |

From Docker, replace `localhost` with `host.docker.internal`.

## How it works

The local provider uses the same OpenAI client as `AGENT_PROVIDER=openai`, pointed at
`AGENT_BASE_URL`, with temperature 0 and an output budget of 8,192 tokens (sent as `max_tokens`,
which every compatible server accepts). It never sends `OPENAI_API_KEY`: when `AGENT_API_KEY` is
empty it sends a placeholder, which local servers ignore. Requests go to
`/v1/chat/completions`: tool calls for the agent's steps, and `response_format` with a JSON schema
for extraction. `services/orchestrator/tests/unit/test_local_provider.py` checks this request
shape against a stand-in server.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Runs end incomplete; the agent stops after a step or two | The model answered in text instead of calling one tool. Try a larger model or one tagged *tools*. |
| Extraction errors about invalid JSON, or values from the wrong document | Context window too small: see step 2. |
| Timeouts | Raise the three timeouts above; check `ollama ps` for CPU offloading. |
| "Connection refused" from Docker | Wrong `AGENT_BASE_URL` (use `host.docker.internal`), Ollama not running, or on Linux, `OLLAMA_HOST` not set. |
| Scanned packets fail | The model has no vision support; use a vision model or accept them as errors. |
