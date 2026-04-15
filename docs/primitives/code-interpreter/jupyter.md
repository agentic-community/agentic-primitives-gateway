# Jupyter Code Interpreter

Self-hosted code execution provider using [Jupyter Server](https://jupyter-server.readthedocs.io/) or Enterprise Gateway. Supports Python and any other Jupyter kernel.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  code_interpreter:
    backend: "agentic_primitives_gateway.primitives.code_interpreter.jupyter.JupyterCodeInterpreterProvider"
    config:
      base_url: "http://localhost:8888"
      token: ""
      kernel_name: "python3"
      execution_timeout: 30.0
      file_root: "/tmp"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `base_url` | `http://localhost:8888` | Jupyter server URL |
| `token` | `""` | Jupyter authentication token |
| `kernel_name` | `python3` | Kernel to use for execution |
| `execution_timeout` | `30.0` | Max seconds per code execution |
| `file_root` | `/tmp` | Root directory for file I/O |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `JUPYTER_URL` | `http://localhost:8888` | Jupyter server URL |
| `JUPYTER_TOKEN` | `""` | Authentication token |
| `JUPYTER_KERNEL` | `python3` | Kernel name |
| `JUPYTER_FILE_ROOT` | `/tmp` | File root directory |

## Running Jupyter Locally

```bash
# Simple Jupyter server (no token)
pip install jupyter
jupyter server --no-browser --port=8888 --ServerApp.token=''

# Or with Docker
docker run -d -p 8888:8888 jupyter/base-notebook start-notebook.py --NotebookApp.token=''
```

## Using the Code Interpreter API

### Start a Session

```bash
curl -X POST http://localhost:8000/api/v1/code-interpreter/sessions \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Execute Code

```bash
curl -X POST http://localhost:8000/api/v1/code-interpreter/sessions/{session_id}/execute \
  -H "Content-Type: application/json" \
  -d '{
    "code": "import math\nprint(math.pi)",
    "language": "python"
  }'
```

### Close Session

```bash
curl -X DELETE http://localhost:8000/api/v1/code-interpreter/sessions/{session_id}
```

## Using with Declarative Agents

```yaml
agents:
  specs:
    coder:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      system_prompt: "You are a coding assistant. Write and execute code."
      primitives:
        code_interpreter:
          enabled: true
      provider_overrides:
        code_interpreter: "jupyter"
```

The agent gets `execute_code` and `upload_file` / `download_file` tools automatically.

## Using with the Python Client

```python
from agentic_primitives_gateway_client import AgenticPlatformClient, CodeInterpreter

client = AgenticPlatformClient("http://localhost:8000")
code = CodeInterpreter(client)

await code.start()
result = await code.execute("print('Hello from Jupyter!')")
print(result["output"])
await code.close()
```

## How It Works

1. **Session creation**: starts a new Jupyter kernel via the Jupyter Server API
2. **Execution**: sends code to the kernel via WebSocket (`execute_request`), collects output from `stream`, `execute_result`, and `error` messages
3. **File I/O**: uses kernel-based file operations (works without the Jupyter Contents REST API)
4. **Session isolation**: each agent run gets its own kernel, tracked in the `SessionRegistry`

## Prerequisites

- `pip install agentic-primitives-gateway[jupyter]`
- Running Jupyter Server instance
