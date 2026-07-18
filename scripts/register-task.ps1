<#
.SYNOPSIS
    Register a Kizashi run as a Windows Scheduled Task.

.DESCRIPTION
    Creates a scheduled task that runs `python -m uv run kizashi-daily` in the
    project root. Two modes:

      * Daily full run (default): collect -> enrich -> dashboard, once per day.
      * Frequent collection (-EveryHours N): collect-only (no enrich, zero API
        cost), repeated through the day via multiple daily triggers. Use this
        to "keep gathering data" without spending on the LLM.

    Runs in the logged-on user's context.

    NOTE: This file is intentionally ASCII-only. Windows PowerShell 5.1 reads
    .ps1 files using the system ANSI code page (cp932 on Japanese Windows),
    so non-ASCII characters here would break the parser.

.PARAMETER Time
    Start time (HH:mm) for the daily full run, or the first trigger when
    -EveryHours is used. Default 07:00.

.PARAMETER EveryHours
    If > 0, repeat through the day every N hours (triggers at Time, Time+N, ...
    within 24h). 0 = single daily run. Default 0.

.PARAMETER Collect
    Run collection only (`kizashi-daily --no-enrich`). Recommended with
    -EveryHours so frequent runs never touch the paid API.

.PARAMETER TaskName
    Task name. Default "KizashiDaily" (or "KizashiCollect" when -Collect).

.EXAMPLE
    # Daily full run at 07:00
    powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1

    # Collect every 3 hours, no enrichment (keep gathering data, free)
    powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1 -EveryHours 3 -Collect

.NOTES
    Unregister : Unregister-ScheduledTask -TaskName <name> -Confirm:$false
    Run now    : Start-ScheduledTask -TaskName <name>
#>
param(
    [string]$Time = "07:00",
    [int]$EveryHours = 0,
    [switch]$Collect,
    [string]$TaskName = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = (Get-Command python).Source

if (-not $TaskName) {
    if ($Collect) { $TaskName = "KizashiCollect" } else { $TaskName = "KizashiDaily" }
}
$argLine = "-m uv run kizashi-daily"
if ($Collect) { $argLine += " --no-enrich" }

Write-Host "Project : $ProjectRoot"
Write-Host "Python  : $Python"
Write-Host "Command : python $argLine"
Write-Host "Task    : $TaskName"

# Build triggers
if ($EveryHours -gt 0) {
    $base = [datetime]::ParseExact($Time, "HH:mm", $null)
    $triggers = @()
    for ($h = 0; $h -lt 24; $h += $EveryHours) {
        $t = $base.AddHours($h)
        $triggers += New-ScheduledTaskTrigger -Daily -At $t.ToString("HH:mm")
    }
    Write-Host "Schedule: every $EveryHours h ($($triggers.Count) triggers/day)"
} else {
    $triggers = @(New-ScheduledTaskTrigger -Daily -At $Time)
    Write-Host "Schedule: daily at $Time"
}

$action = New-ScheduledTaskAction -Execute $Python -Argument $argLine -WorkingDirectory $ProjectRoot

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Overwriting existing task..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Description "Kizashi: collect AI trends (and enrich/dashboard on full runs)" | Out-Null

Write-Host ""
Write-Host "[OK] Registered task '$TaskName'." -ForegroundColor Green
Write-Host "  Run now    : Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Status     : Get-ScheduledTask -TaskName $TaskName"
Write-Host "  Unregister : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
