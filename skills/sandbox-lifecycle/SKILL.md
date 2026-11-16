---
name: sandbox-lifecycle
description: Provision, clone, checkpoint, restore, and destroy isolated Windows sandboxes via the computer-use MCP server. Use when spinning up a clean sandbox, snapshotting before risky actions, or tearing down ephemeral instances.
---

# Sandbox Lifecycle

## When to Use Ephemeral vs Persistent VMs
- **Ephemeral (`mode="ephemeral"`)**: one-off tasks, untrusted code/installers, clean-room testing. Destroy with `destroy_vm(vm_id)` when finished.
- **Persistent (`mode="persistent"`)**: long-lived environments with installed apps, logins, or game state.

## Lifecycle Workflow
1. Check existing VMs with `list_vms`.
2. Ensure the host admin broker is running (elevated PowerShell): `scripts/admin-broker.ps1`.
3. Clone a fresh sandbox from `base_vhdx` (`config/settings.local.json`):
   - `create_vm(name="agent-01", mode="ephemeral", memory_gb=4, cpu_count=2)`
   - Creates a differencing VHDX, assigns a unique static virtual MAC, starts the VM, and opens the host PiP thumbnail.
4. If guest MCP files are not yet on the cloned VM, deploy from host:
   - `scripts/deploy-guest.ps1`
5. Before risky OS/app changes, create a snapshot:
   - `checkpoint_vm(vm_id, name="pre-install")`
   - Roll back anytime with `restore_vm(vm_id, checkpoint_name="pre-install")`.
6. Clean up ephemeral sandboxes when done:
   - `destroy_vm(vm_id)` stops the VM, removes it from Hyper-V, and deletes its differencing VHDX.
