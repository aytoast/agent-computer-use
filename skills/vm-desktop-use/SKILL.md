---
name: vm-desktop-use
description: Operate the Hyper-V Windows VM desktop through the hyperv-local MCP server (`list_vms`, `screenshot`, `click`, `batch`, `exec_powershell`). Use when automating apps or running diagnostics inside the configured Hyper-V guest.
---

# VM Desktop Use

## Tools
- Discovery: `list_vms`, `agent_status`, `list_windows`, `window_snapshot`
- Capture: `screenshot` (dynamically handles `UnityWndClass` and standard windows)
- Input: `click`, `move`, `drag`, `scroll`, `key`, `type`, `wait`, `batch`
- Guest shell: `exec_powershell`

## Workflow
1. Verify VM and guest state with `list_vms` and `agent_status`.
2. Resolve target window via `list_windows` and capture fresh frame with `screenshot`.
3. Execute input actions (use `batch` for multi-step flows with integrated task context and confirmation).
4. Re-capture `screenshot` to verify UI state before continuing.

## Maintenance Scripts
- Deploy updated guest code (elevated PowerShell): `scripts/deploy-guest.ps1`
- Admin pipe client: `scripts/admin-request.ps1 -Operation <ping|list_vms|agent_status|restart_agent|shutdown>`
- Run unit tests: `python -m unittest discover -s tests`
