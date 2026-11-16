# agent-computer-use

**Local Windows sandbox and computer-use MCP server on Hyper-V.** Give AI agents (Claude Code, Claude Desktop, Cursor, Codex, VS Code, Antigravity, ChatGPT) full desktop control (`screenshot`, `click`, `move`, `drag`, `scroll`, `key`, `type`, `batch`, `exec_powershell`) inside isolated, ephemeral Windows VMs—without touching your host desktop or paying for cloud VMs.

## The problem

Giving an AI agent computer-use access usually forces a bad tradeoff:

1. **Run on your host desktop**: the agent fights you for your mouse and keyboard, clicks the wrong window, and has direct access to your personal files, browser sessions, and credentials.
2. **Rent a cloud computer / remote sandbox**: you pay per-minute GPU/VM fees, upload private code or files over the internet, deal with video/input latency, and lose access to local Windows apps, LAN resources, and hardware.

## Same architecture as cloud computers—100% local

Cloud agent sandboxes work by cloning a clean base disk (copy-on-write), booting an isolated OS, exposing a control API over an internal network, and destroying the delta disk when the task ends.

`agent-computer-use` implements that exact same pattern natively on Windows:

- **Instant ephemeral sandboxes**: clones a parent `win11-agent-base.vhdx` using Hyper-V differencing VHDX disks in seconds.
- **Checkpoints & rollback**: snapshot (`checkpoint_vm`) and restore (`restore_vm`) VM state before risky GUI or system actions.
- **Zero host interference**: agents interact strictly with the guest virtual screen and Win32 `SendInput` inside the VM while you keep using your host normally.
- **Live host PiP notch**: watch any active Agent VM in a lightweight native DWM picture-in-picture overlay on your host without stealing focus.

## Why this stack

| Layer | Choice | Why |
| :--- | :--- | :--- |
| **Hypervisor & Disks** | Windows Hyper-V + Differencing VHDX | Native Type-1 hypervisor built into Windows Pro/Enterprise; copy-on-write VHDX clones boot fast and tear down cleanly with zero cloud cost. |
| **Host <-> Guest Transport** | Hyper-V Default Switch + MAC-verified IP + PowerShell Direct | Resolves each VM exclusively by its static virtual MAC address over the private Hyper-V switch—no exposed LAN/WAN ports. |
| **MCP Protocol & Input** | FastMCP + Python + Win32 `SendInput` / `ImageGrab` | Stdio MCP server on the host (`hyperv-acu`) proxies to an authenticated HTTP FastMCP server (`vm_agent_server.py`) inside each guest VM. |
| **Host Preview** | Native DWM Thumbnail (`DwmRegisterThumbnail`) | Hardware-composited live PiP overlay (`pip_thumbnail.py`) with auto-hide notch mode for every VM with guest MCP online. |

## Quickstart (IDE-driven)

**Prerequisites**: Windows 10/11 Pro, Enterprise, or Server with Hyper-V enabled and Python 3.11+.

Install the package and register the MCP server across detected IDEs (`claude-desktop`, `cursor`, `codex`, `antigravity`, `vscode`, `claude-code`):

```powershell
pip install -e .
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -InstallClients all
```

From there, your IDE or AI agent drives VM provisioning and lifecycle directly through MCP tools (`create_vm`, `start_vm`, `checkpoint_vm`, `restore_vm`, `destroy_vm`)—no manual step-by-step guest setup required.

<details>
<summary>Manual per-IDE MCP registration snippets</summary>

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

</details>

## Available MCP tools

All observation and input tools operate **desktop-wide by default** (full guest virtual screen) and accept an optional `target_id` when scoping to a specific window:

- **VM lifecycle**: `create_vm`, `destroy_vm`, `start_vm`, `stop_vm`, `checkpoint_vm`, `restore_vm`
- **Discovery**: `list_vms`, `agent_status`, `list_windows`
- **Observation**: `screenshot` (full desktop + visible `windows` bounds `rect` / `client_rect` relative to `(0, 0)`, or pass `target_id`), `window_snapshot`
- **Input**: `click`, `move`, `drag`, `scroll`, `key`, `type`, `wait`, `batch`
- **Execution**: `focus_window`, `exec_powershell`

## Safety guardrails

- **Zero host risk**: agents operate strictly inside the sandboxed Hyper-V VM (`vm_id` + MAC-verified private IP).
- **Mandatory visual verification**: every input requires a fresh screenshot before acting and post-action verification.
- **Human-takeover safe**: automatically pauses/refuses agent input when host or guest user activity is detected.
- **Crash-safe lock**: single-action guard (`runtime/hyperv-input-block.json`) blocks further input after uncertain outcomes until reviewed.

## Project layout

- `src/hyperv_acu/`: host MCP server, guest proxy, VM pool, policy/input guards, DWM PiP overlay
- `guest/`: guest-side FastMCP server (`vm_agent_server.py`) and Win32 capture/input modules
- `config/`: `settings.json` defaults and `settings.example.json` (local overrides in gitignored `settings.local.json`)
- `scripts/`: `setup.ps1`, `install-mcp.ps1`, `provision-base.ps1`, `deploy-guest.ps1`, `admin-broker.ps1`, `admin-request.ps1`
- `tests/`: unit test suite (`python -m unittest discover -s tests -t .`)
- `tunnel/`: OpenAI tunnel config template (`hyperv.yaml.example`)

## FAQ

**How is this different from Playwright or browser-use MCP?**
Browser tools only automate DOM/webpages. `agent-computer-use` controls an entire Windows OS—native desktop apps, installers, terminals, system dialogs, and browsers—inside an isolated VM.

**Do I need cloud GPUs or external APIs to host the sandbox?**
No. Everything runs locally on Windows Hyper-V using differencing VHDX images and local virtual networking.

**Can I run multiple isolated Agent VMs at once?**
Yes. `vm_pool.py` tracks multiple managed VMs (`ephemeral` or `persistent`) up to `max_managed_vms`, routes each tool call by `vm_id` and virtual MAC, and launches a dedicated host PiP thumbnail for each active VM.

## License

MIT
