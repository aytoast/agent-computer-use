# agent-computer-use

Isolated Windows VM computer-use MCP server for AI agents (Claude, Codex, Cursor, VS Code, Antigravity, ChatGPT) running on Hyper-V.

Gives agents full desktop control (`screenshot`, `click`, `move`, `drag`, `scroll`, `key`, `type`, `batch`, `exec_powershell`) inside a sandboxed Windows Hyper-V VM—never touching your host desktop.

## Prerequisites

- **Host**: Windows 10/11 Pro/Enterprise/Server with Hyper-V enabled and Python 3.11+
- **Guest VM**: Windows VM on Hyper-V Default Switch with PowerShell Direct enabled, a dedicated local user account, and Python 3.11+ (`fastmcp`, `mcp`, `psutil`, `pywin32`, `pillow`)

## One-command install

Install Python dependencies, auto-detect your Hyper-V VM, and register the MCP server in all detected IDEs (`claude-desktop`, `cursor`, `codex`, `antigravity`, `vscode`, `claude-code`):

```powershell
pip install -e .
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -InstallClients all
```

Or target specific clients:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-mcp.ps1 -Clients cursor,claude-desktop,codex
```

### Per-IDE manual setup

- **Claude Code**: `claude mcp add -s user hyperv-computer-use -- python -B -u src/hyperv_acu/server.py`
- **Claude Desktop** (`%APPDATA%\Claude\claude_desktop_config.json`), **Cursor** (`~/.cursor/mcp.json`), **Antigravity** (`~/.gemini/config/mcp_config.json`):

```json
{
  "mcpServers": {
    "hyperv-computer-use": {
      "command": "python",
      "args": ["-B", "-u", "C:/path/to/agent-computer-use/src/hyperv_acu/server.py"]
    }
  }
}
```

- **Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.hyperv-computer-use]
command = "python"
args = ["-B", "-u", "C:/path/to/agent-computer-use/src/hyperv_acu/server.py"]
```

- **ChatGPT (OpenAI tunnel)**: copy `tunnel/hyperv.yaml.example` to `tunnel/hyperv.yaml` and run `scripts\connect-tunnel.ps1`.

## Guest VM bootstrap

1. Create local user inside Hyper-V guest (username `Agent`, password `123456`, security answers: `new york`, `john`, `washington dc`) and install Python dependencies (`pip install fastmcp mcp psutil pywin32 pillow`).
2. Copy `.env.example` to `.env` and set paths for `HYPERV_GUEST_TOKEN_PATH` and `HYPERV_GUEST_PASSWORD_PATH`.
3. Create a scheduled task on the guest (default `HyperV-VM-Agent`) running `pythonw.exe C:\Users\Agent\WindowsAgent\vm-agent-server.py` at logon, then deploy guest files from the host:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deploy-guest.ps1
```

## Safety guardrails

- **Zero host risk**: Agents operate strictly inside the configured Hyper-V VM (`vm_id` + MAC-verified private IP).
- **Mandatory visual verification**: Every input requires a fresh screenshot of the target window and post-action verification.
- **Human-takeover safe**: Automatically refuses input when the host or guest user is active or the agent is paused.
- **Crash-safe state**: Single-action lock (`runtime/hyperv-input-block.json`) blocks further input after uncertain outcomes until reviewed.

## Available MCP tools

- **Discovery**: `list_vms`, `agent_status`, `list_windows`
- **Observation**: `screenshot`, `window_snapshot`
- **Input**: `click`, `double_click`, `move`, `drag`, `scroll`, `key`, `type`, `wait`, `batch`
- **Execution**: `focus_window`, `exec_powershell`

## Project layout

- `src/hyperv_acu/`: host MCP server, guest proxy, policy/input guards, PiP overlay
- `guest/`: guest-side Windows MCP server and capture/input modules
- `config/`: `settings.json` defaults + `settings.example.json` (local overrides in `settings.local.json`)
- `scripts/`: `setup.ps1`, `install-mcp.ps1`, `deploy-guest.ps1`, `admin-broker.ps1`, `admin-request.ps1`, `connect-tunnel.ps1`
- `tests/`: unit tests (`python -m unittest discover -s tests -t .`)
- `tunnel/`: OpenAI tunnel config template (`hyperv.yaml.example`)

## License

MIT
