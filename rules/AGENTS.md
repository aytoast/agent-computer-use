# agent-computer-use rules

- operate only the configured Hyper-V guest via `hyperv-local` MCP tools; never touch host desktop.
- call `list_vms`, `list_windows`, and `screenshot` before sending pointer/keyboard input.
- prefer `batch` for multi-step sequences (`click`, `move`, `drag`, `scroll`, `key`, `type`, `wait`, `screenshot`).
- inspect fresh `screenshot` after actions; stop immediately on user takeover, pause, or refused input.
