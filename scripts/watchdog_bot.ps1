# Güzel Tahmin Bot Watchdog Script
# This script monitors the Telegram Bot process and restarts it if it crashes or stops.

$ScriptDir = $PSScriptRoot
$WorkingDirectory = Split-Path -Parent $ScriptDir
$Interpreter = "$WorkingDirectory\.venv\Scripts\python.exe"
$Arguments = "-m app.telegram_bot"

# Check if the bot process is currently running
$Process = Get-CimInstance Win32_Process -Filter "CommandLine like '%app.telegram_bot%'"

if ($Process -eq $null) {
    # Write alert log
    $LogMsg = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - [WATCHDOG] Bot is not running. Launching a new instance..."
    Write-Output $LogMsg
    
    # Ensure data directory exists
    $DataDir = "$WorkingDirectory\data"
    if (-not (Test-Path $DataDir)) {
        New-Item -ItemType Directory -Force -Path $DataDir
    }
    
    Add-Content -Path "$DataDir\watchdog.log" -Value $LogMsg

    # Start the process in the background with redirected output to prevent stdout crashes
    $OutLog = "$DataDir\bot_stdout.log"
    $ErrLog = "$DataDir\bot_stderr.log"
    
    Start-Process -FilePath $Interpreter -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -WindowStyle Hidden
} else {
    # Log that the bot is healthy
    $LogMsg = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - [WATCHDOG] Bot is healthy. Process ID: $($Process.ProcessId)"
    Write-Output $LogMsg
}
