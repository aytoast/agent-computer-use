# agent-computer-use

**Give AI agents a real Windows desktop—without giving up yours.**

`agent-computer-use` spins up isolated, ephemeral Windows sandboxes on local Hyper-V and exposes full desktop control (`screenshot`, `click`, `move`, `drag`, `scroll`, `key`, `type`, `batch`, `exec_powershell`) over MCP.

You keep working on your host. Your agent works inside its own sandboxed VM, with a live picture-in-picture notch on your screen—double-click it anytime to open the VM window and take over.

<!-- Drop demo GIF here once recorded: ![agent-computer-use demo](docs/media/demo.gif) -->

## Why not run on your host or a cloud VM?

| | Host Desktop | Cloud Sandbox | **agent-computer-use** |
| :--- | :--- | :--- | :--- |
| **Mouse & keyboard** | Fights you for control | Isolated | **Isolated** |
| **Credentials & files** | Exposed to agent | Uploaded to cloud | **Stays local** |
| **Cost & latency** | Free, local | Per-minute billing + lag | **Free, local** |
| **Reset / rollback** | Manual cleanup | Snapshot / destroy | **Instant VHDX clone & checkpoint** |

## Quickstart

Requires Windows 10/11 Pro, Enterprise, or Server with Hyper-V enabled and Python 3.11+.

```powershell
pip install -e .
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -InstallClients all
```

`setup.ps1` registers `computer-use` in your detected IDEs (`claude-code`, `claude-desktop`, `cursor`, `codex`, `antigravity`, `vscode`). From there, your agent handles VM provisioning, cloning, checkpoints, and teardown via MCP tools.

<details>
<summary>Manual per-IDE MCP config</summary>

- **Claude Code**: `claude mcp add -s user computer-use -- python -B -u src/hyperv_acu/server.py`
- **Claude Desktop** (`%APPDATA%\Claude\claude_desktop_config.json`), **Cursor** (`~/.cursor/mcp.json`), **Antigravity** (`~/.gemini/config/mcp_config.json`):

```json
{
  "mcpServers": {
    "computer-use": {
      "command": "python",
      "args": ["-B", "-u", "C:/path/to/agent-computer-use/src/hyperv_acu/server.py"]
    }
  }
}
```

- **Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.computer-use]
command = "python"
args = ["-B", "-u", "C:/path/to/agent-computer-use/src/hyperv_acu/server.py"]
```

- **ChatGPT / Codex tunnel**: copy `.codex-plugin/tunnel.yaml.example` to `.codex-plugin/tunnel/hyperv.yaml` and run `scripts\connect-tunnel.ps1`.

</details>

## What the agent gets

All observation and input tools operate **desktop-wide by default** (with optional `target_id` for window-level scoping):

- **Lifecycle**: `create_vm`, `start_vm`, `stop_vm`, `checkpoint_vm`, `restore_vm`, `destroy_vm`
- **Vision**: `screenshot` (full virtual screen + visible window bounds), `window_snapshot`, `list_windows`
- **Input**: `click`, `move`, `drag`, `scroll`, `key`, `type`, `wait`, `batch`
- **Shell**: `exec_powershell`

## How it stays safe

- **Same pattern as cloud sandboxes**: copy-on-write differencing VHDX clones (`win11-agent-base.vhdx`) boot in seconds and delete cleanly on `destroy_vm`.
- **MAC-locked networking**: resolves each VM strictly by static virtual MAC over the private Hyper-V switch—no exposed ports.
- **Human-takeover safe**: pauses/refuses agent input when you interact with the VM; double-click the host PiP thumbnail to jump straight into `vmconnect.exe`.

<details>
<summary>Project layout & tests</summary>

- `src/hyperv_acu/`: host MCP server, VM pool, guest proxy, safety guards, DWM PiP overlay
- `guest/`: guest-side FastMCP server (`agent_server.py`) and Win32 capture/input modules
- `skills/`: agent skills (`computer-use`, `sandbox-lifecycle`)
- `config/`: `settings.json` defaults + `settings.example.json`
- `scripts/`: `setup.ps1`, `install-mcp.ps1`, `provision-base.ps1`, `deploy-guest.ps1`, `admin-broker.ps1`
- `.codex-plugin/`: Codex plugin manifest + `tunnel.yaml.example`
- Run tests: `python -m unittest discover -s tests -t .`

</details>

## License

MIT
