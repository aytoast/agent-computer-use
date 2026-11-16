"""Authenticated Windows MCP server for the Hyper-V guest virtual machine."""
from pathlib import Path
import os
import sys

ROOT = os.environ.get('HYPERV_GUEST_ROOT', str(Path(__file__).resolve().parent))
# Windows multiprocessing imports this module again in each worker. Keep DLL
# directories alive there, but start the MCP server only in the main process.
_DLL_DIRS = [
    os.add_dll_directory(ROOT + r'\runtime\Scripts'),
    os.add_dll_directory(ROOT + r'\runtime\Lib\site-packages\pywin32_system32'),
]
sys.path.insert(0, ROOT + r'\app')


def main():
    # The scheduled task's console belongs to this service, not the game.
    # Hide it so it cannot intercept guest clicks; logs remain redirected.
    import ctypes
    from ctypes import wintypes
    ctypes.windll.kernel32.GetConsoleWindow.restype = wintypes.HWND
    ctypes.windll.user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    console = ctypes.windll.kernel32.GetConsoleWindow()
    if console:
        ctypes.windll.user32.ShowWindow(console, 0)
    import atexit
    def _restore_system_cursors():
        ctypes.windll.user32.SystemParametersInfoW(0x0057, 0, None, 0)
    _restore_system_cursors()
    atexit.register(_restore_system_cursors)

    from windows_agent.server import create_server
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    with open(ROOT + r'\mcp-token.txt', encoding='utf8') as token_file:
        token = token_file.read().strip()
    server = create_server(False)
    from fastmcp.utilities.types import Image
    import json
    import uuid
    agent_instance_id = uuid.uuid4().hex

    @server.tool(name='HyperVAgentInstance', annotations={'readOnlyHint': True})
    def hyperv_agent_instance():
        """Identify this guest control-service instance for isolation checks."""
        return {'instance_id': agent_instance_id}

    @server.tool(name='HyperVDesktopScreenshot', annotations={'readOnlyHint': True})
    def hyperv_desktop_screenshot():
        """Capture the entire guest virtual screen for PiP display."""
        import ctypes
        from ctypes import wintypes
        from io import BytesIO
        from PIL import ImageGrab

        user32 = ctypes.WinDLL('user32', use_last_error=True)
        set_context = getattr(user32, 'SetThreadDpiAwarenessContext', None)
        previous = None
        if set_context:
            set_context.argtypes = [wintypes.HANDLE]
            set_context.restype = wintypes.HANDLE
            previous = set_context(ctypes.c_void_p(-4))
        try:
            image = ImageGrab.grab(all_screens=True)
            stream = BytesIO()
            image.save(stream, format='PNG')
            return [Image(data=stream.getvalue(), format='png')]
        finally:
            if previous:
                set_context(previous)

    @server.tool(name='ExecPowerShell')
    def exec_powershell(command: str, timeout_seconds: int = 30, working_directory: str | None = None):
        """Execute a PowerShell command inside the guest and await its output."""
        import base64
        import subprocess
        if not isinstance(command, str) or not command.strip():
            raise ValueError('PowerShell command must be a non-empty string')
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
            raise ValueError('timeout_seconds must be an integer between 1 and 300')
        if working_directory is not None and (not isinstance(working_directory, str) or not working_directory.strip()):
            raise ValueError('working_directory must be a non-empty string when provided')
        encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
        args = [
            'powershell.exe',
            '-NoProfile',
            '-NonInteractive',
            '-ExecutionPolicy',
            'Bypass',
            '-EncodedCommand',
            encoded,
        ]
        try:
            proc = subprocess.run(
                args,
                cwd=working_directory,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout_seconds,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            return {
                'ok': proc.returncode == 0,
                'returncode': proc.returncode,
                'stdout': proc.stdout,
                'stderr': proc.stderr,
                'timed_out': False,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                'ok': False,
                'returncode': None,
                'stdout': exc.stdout.decode('utf-8', errors='replace') if isinstance(exc.stdout, bytes) else (exc.stdout or ''),
                'stderr': exc.stderr.decode('utf-8', errors='replace') if isinstance(exc.stderr, bytes) else (exc.stderr or ''),
                'timed_out': True,
            }

    server.auth = StaticTokenVerifier({token: {'client_id': 'hyperv-local', 'scopes': []}})
    server.run(transport='streamable-http', host='0.0.0.0', port=18777)


if __name__ == '__main__':
    main()
