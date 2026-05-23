$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunScript = Join-Path $RepoRoot "scripts\run_daily.ps1"
$TaskName = "ML_Medical_Research_AutoCollect"

try {
    $Action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`""

    $LogonTrigger = New-ScheduledTaskTrigger -AtLogOn
    $LogonTrigger.Delay = "PT2M"

    $DailyTrigger = New-ScheduledTaskTrigger -Daily -At 8:20AM

    $Settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -RunOnlyIfNetworkAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2)

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger @($LogonTrigger, $DailyTrigger) `
        -Settings $Settings `
        -Description "Collect and organize machine learning resources for medical research after user logon and daily at 08:20." `
        -Force | Out-Null

    Write-Host "Registered scheduled task: $TaskName"
}
catch {
    $Startup = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
    if (-not $Startup) {
        throw
    }

    $ShortcutPath = Join-Path $Startup "$TaskName.lnk"
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "powershell.exe"
    $Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`""
    $Shortcut.WorkingDirectory = $RepoRoot
    $Shortcut.WindowStyle = 7
    $Shortcut.Save()

    Write-Host "Scheduled task registration failed; created Startup shortcut instead."
    Write-Host "Startup shortcut: $ShortcutPath"
}

Write-Host "Run script: $RunScript"
