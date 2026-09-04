---
description: >-
  Install claudectl with pipx or pip, run it from a checkout, add it to a Claude Code
  session as a plugin, and set up the desktop app window and shortcuts.
---

# Installation

## Requirements

- Python 3.10+
- Windows, macOS or Linux
- [Claude Code CLI](https://docs.anthropic.com/claude-code) installed (auto-detected at `~/.local/bin/` or on PATH; overridable in Settings)
- Any text editor — Notepad++ / VS Code / `$EDITOR` are auto-detected (overridable in Settings)

No API key: claudectl uses the Claude Code authentication you already have. No third-party
packages — it is pure Python standard library.

## Setup

### Installing it as a command

```
pipx install claudectl     # or: pip install claudectl
claudectl
```

That gives you `claudectl`, `claudectl --gui`, `claudectl review`,
`claudectl recall "<topic>"` and `claudectl statusline` from anywhere.

### Clone and run

```
git clone https://github.com/babarmuhammad/claudectl.git
cd claudectl
python claude-sessions.py
```

There is nothing to build and no dependencies to install. On Windows you can double-click
`Open Repo cmd.bat` instead of using a terminal.

To put the command on your PATH from a checkout — for development, or to run an unreleased
change:

```
pip install -e .        # or: pipx install .
```

### Inside a Claude Code session

claudectl also ships as a Claude Code plugin, which puts its three slash commands and its
eight skills inside the session itself:

```
/plugin marketplace add babarmuhammad/claudectl
/plugin install claudectl@claudectl
```

It is independent of the CLI install, and it deliberately ships no hooks. See
[Claude Code plugin](plugin.md).

### GUI setup

The [desktop app](desktop.md) needs no extra dependencies for the Edge/browser shells. For the
native window install PyQt6 (optional):

```
pip install PyQt6 PyQt6-WebEngine
```

Start it with:

```
python claude-sessions.py --gui   # from the checkout
claudectl --gui                   # after `pip install -e .`
```

`gui_shell` in Settings picks the window: `auto` (Qt → Edge app window → browser), `qt`,
`edge`, or `browser`. The bottom-left **TUI/GUI** toggle (or the `ui_mode` setting) selects
which interface starts by default; `--tui` / `--gui` always override.

**Desktop shortcut with the GUI icon** — the GUI has its own icon (`claudectl-gui.ico`,
regenerate with `py tools/make_gui_icon.py`). `pythonw.exe` runs it without a console
window:

```powershell
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut("$env:USERPROFILE\Desktop\claudectl GUI.lnk")
$lnk.TargetPath       = "$env:LOCALAPPDATA\Programs\Python\Python310\pythonw.exe"
$lnk.Arguments        = "`"$PWD\claude-sessions.py`" --gui"
$lnk.WorkingDirectory = "$PWD"
$lnk.IconLocation     = "$PWD\claudectl-gui.ico, 0"
$lnk.Save()
```

## Windows shortcuts & taskbar pin

??? note "Desktop shortcut, taskbar pin and elevated launch"

    **Desktop shortcut** — right-click `Open Repo cmd.bat` → **Send to** →
    **Desktop (create shortcut)**.

    **Pin to taskbar (Windows 11)** — Windows 11 can't pin `.bat` shortcuts directly; the
    shortcut must point to `cmd.exe`. Run this once in PowerShell from the repo folder:

    ```powershell
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut("$env:USERPROFILE\Desktop\Open Repo Claude.lnk")
    $lnk.TargetPath       = "C:\Windows\System32\cmd.exe"
    $lnk.Arguments        = "/c `"$PWD\Open Repo cmd.bat`""
    $lnk.WorkingDirectory = "$PWD"
    $lnk.IconLocation     = "$PWD\claudectl.ico, 0"
    $lnk.Save()
    ```

    Then right-click the Desktop shortcut → **Pin to taskbar**.

    **Elevated shortcut, no repeated UAC prompt** — if `claude.exe` or your project paths
    need admin rights, a plain "Run as administrator" shortcut checkbox triggers a UAC
    prompt on every launch. To elevate once and skip the prompt afterward, register a
    Scheduled Task that already runs at highest privilege, then point the shortcut at
    `schtasks /run`:

    ```powershell
    # 1) register the task (one-time)
    $action    = New-ScheduledTaskAction -Execute "C:\Users\<you>\AppData\Local\Microsoft\WindowsApps\wt.exe" -Argument '-d "<repo>" powershell -Command "& ''<repo>\Open Repo cmd.bat''"' -WorkingDirectory "<repo>"
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName "ClaudeCtl" -Action $action -Principal $principal -Settings $settings -Force

    # 2) point the shortcut at the task instead of launching directly
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut("$env:USERPROFILE\Desktop\claudectl.lnk")
    $lnk.TargetPath       = "C:\Windows\System32\schtasks.exe"
    $lnk.Arguments        = '/run /tn "ClaudeCtl"'
    $lnk.WorkingDirectory = "<repo>"
    $lnk.IconLocation     = "<repo>\claudectl.ico, 0"
    $lnk.Save()
    ```

    Leave the shortcut's own **"Run as administrator"** checkbox unticked — `schtasks.exe`
    itself doesn't need to be elevated, only the task it triggers. Launching via `wt.exe`
    (instead of `cmd.exe`/`powershell.exe` directly) also avoids the legacy-conhost fallback
    that elevated console apps can trigger, which otherwise makes the TUI render with broken
    colors/box-drawing under UAC.

## Next steps

- [Quickstart](quickstart.md) — install to first session in five minutes
- [Terminal UI](tui.md) — every screen and every key binding
- [Command line](cli.md) — every command, for scripts and hooks
- [Installing the agent library](agent-library.md) — bulk-install 150+ community subagents
