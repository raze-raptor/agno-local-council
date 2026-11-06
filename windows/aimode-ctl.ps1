# CLI for AI Mode:  aimode-ctl.ps1 status | on | off | unload | simulate-game <name> | end-simulated-game | log
param([Parameter(Position = 0)][string]$Cmd = 'status', [Parameter(Position = 1, ValueFromRemainingArguments = $true)]$Rest)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
switch ($Cmd) {
    'status' { Get-Content (Join-Path $root 'state.json') -Raw }
    'log'    { Get-Content (Join-Path $root 'aimode.log') -Tail 25 }
    default  { $c = $Cmd; if ($Rest) { $c = "$Cmd $($Rest -join ' ')" }
               New-Item -ItemType Directory -Force -Path (Join-Path $root 'control') | Out-Null
               Set-Content -Path (Join-Path $root 'control\cmd.txt') -Value $c -Encoding ASCII; "sent: $c (the tray app reads it within 5 s)" }
}
