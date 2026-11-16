# agent-computer-use rules

- operate only inside Hyper-V guest VMs via `hyperv-computer-use` MCP tools; never touch host desktop.
- call `list_vms` and `screenshot(vm_id)` before sending pointer/keyboard input.
- all input tools (`click`, `move`, `drag`, `scroll`, `key`, `type`, `batch`) are desktop-wide by default; pass `target_id` only when scoping to a specific window.
- prefer `exec_powershell` for non-GUI tasks and `batch` for multi-step GUI flows.
- checkpoint before risky guest changes (`checkpoint_vm`) and destroy ephemeral VMs when done (`destroy_vm`).
- inspect fresh `screenshot` after actions; stop immediately on user takeover, pause, or refused input.
