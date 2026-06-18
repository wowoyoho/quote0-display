# Schedule quote0-display to run every 10 minutes on Windows Task Scheduler.
# Run this script once from PowerShell (as Administrator is NOT required).
#
# Usage:
#   .\schedule_windows.ps1
#
# To remove the task later:
#   Unregister-ScheduledTask -TaskName "Quote0Display" -Confirm:$false

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$PythonExe  = (Get-Command python).Source   # or set explicitly: "C:\Python312\python.exe"
$Script     = Join-Path $ScriptDir "display.py"

# ── Set your credentials here (or they can live in a .env file) ──────────────
$Env = @(
    New-ScheduledTaskSettingsSet   # placeholder; env vars set below
)

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$Script`"" `
    -WorkingDirectory $ScriptDir

# Repeat every 10 minutes, indefinitely
$Trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 10) `
    -Once -At (Get-Date)

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName   "Quote0Display" `
    -Action     $Action `
    -Trigger    $Trigger `
    -Settings   $Settings `
    -RunLevel   Limited `
    -Force

Write-Host "Task registered. It will run every 10 minutes."
Write-Host "To run immediately: Start-ScheduledTask -TaskName 'Quote0Display'"
Write-Host "To view logs:       Get-ScheduledTaskInfo -TaskName 'Quote0Display'"
Write-Host ""
Write-Host "NOTE: Set QUOTE0_API_KEY and QUOTE0_DEVICE_ID in your .env file"
Write-Host "      or add them to the task's environment in Task Scheduler GUI."
