---
name: vm-desktop-use
description: Operate an isolated Hyper-V Windows VM desktop through the hyperv-computer-use MCP server (`list_vms`, `screenshot`, `click`, `move`, `drag`, `scroll`, `key`, `type`, `batch`, `exec_powershell`). Use when automating apps, browsers, or OS tasks inside a sandboxed VM.
---

# VM Desktop Use

## Core Rules
- Operate strictly inside the target Hyper-V VM (`vm_id`); never touch the host desktop.
- All observation and input tools (`screenshot`, `click`, `move`, `drag`, `scroll`, `key`, `type`, `batch`) are **desktop-wide by default**.
- Take a fresh `screenshot(vm_id)` before sending pointer/keyboard input, and re-capture after actions to verify state.
- Prefer `exec_powershell` over GUI clicking for file ops, installs, registry/service checks, or CLI tasks.
- Prefer `batch` for multi-step GUI sequences (`click`, `double_click`, `move`, `drag`, `scroll`, `key`, `type`, `wait`, `screenshot`).
- Stop immediately if input is refused (`user_active`, `paused`, or pending input lock).

## Workflow
1. Call `list_vms` to find a `Running` VM with `control_configured: true` and `guest_agent_connected: true`.
2. Call `screenshot(vm_id)` to capture the full desktop. Metadata includes `width`, `height`, and `windows` (`hwnd`, `title`, `rect`, `client_rect` relative to `(0, 0)`).
3. Send desktop-wide actions (`click(vm_id, x, y)`, `type(vm_id, text)`, `key(vm_id, "enter")`, `batch(vm_id, actions=[...])`). Use `coordinate_space="normalized_1000"` if working in `0..1000` coordinates.
4. Optional window scoping: if targeting a specific window (`target_id` from `list_windows`), convert desktop coordinates using `client_rect` from `screenshot`:
   - `local_x = screen_x - client_rect.x`
   - `local_y = screen_y - client_rect.y`
5. Re-capture `screenshot(vm_id)` to confirm the result.
