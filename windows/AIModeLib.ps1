# AI Mode library: pure-ish functions, no GUI. Dot-sourced by AIMode.ps1 and by selftest.ps1.
# Rules from the owner: never realtime/High priority, never touch GPU clocks/voltage/BIOS/drivers, gaming always wins, launchers are not games.

$script:Root = Split-Path -Parent $MyInvocation.MyCommand.Path
function Get-AIConfig { Get-Content (Join-Path $script:Root 'config.json') -Raw | ConvertFrom-Json }

function Write-AILog([string]$msg) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    $p = Join-Path $script:Root 'aimode.log'
    try { Add-Content -Path $p -Value $line -Encoding UTF8
          if ((Get-Item $p).Length -gt 1MB) { Move-Item $p "$p.old" -Force } } catch {}
}

# ---------- game detection ----------
# Returns a LABEL (never the command line, which can contain an access token) or $null.
function Test-GameProcess($name, $path, $cmdline, $cfg) {
    $g = $cfg.gaming
    if ($g.never_games -contains $name) { return $null }                      # launchers / recorders / Roblox Studio
    if ($g.minecraft_java.process_names -contains $name) {                      # Java Minecraft = the game process itself
        if ($cmdline -and ($cmdline -match $g.minecraft_java.cmdline_regex)) { return 'minecraft-java' }
        return $null
    }
    if ($g.exe_names -contains $name) { return $name }
    if ($path) {
        foreach ($x in $g.path_excludes) { if ($path.IndexOf($x, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $null } }
        foreach ($pre in $g.path_prefixes) { if ($path.StartsWith($pre, [StringComparison]::OrdinalIgnoreCase)) { return $name } }
    }
    return $null
}

function Find-RunningGame($cfg) {
    try {
        $procs = Get-CimInstance Win32_Process -Property Name, ProcessId, ExecutablePath, CommandLine -ErrorAction Stop
    } catch { return $null }
    foreach ($p in $procs) {
        $lbl = Test-GameProcess $p.Name $p.ExecutablePath $p.CommandLine $cfg
        if ($lbl) { return $lbl }
    }
    return $null
}

# ---------- machine metrics / safety ----------
function Get-AIMetrics($cfg) {
    $os = Get-CimInstance Win32_OperatingSystem
    $limit = $os.TotalVirtualMemorySize / 1MB; $used = ($os.TotalVirtualMemorySize - $os.FreeVirtualMemory) / 1MB
    $vram = $null
    try { $vram = ((Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage' -ErrorAction Stop).CounterSamples | Measure-Object CookedValue -Maximum).Maximum / 1MB } catch {}
    [pscustomobject]@{
        ram_available_mb = [int]($os.FreePhysicalMemory / 1KB)
        commit_pct       = [math]::Round(100 * $used / $limit, 1)
        vram_used_mib    = if ($vram -ne $null) { [int]$vram } else { $null }
        vram_free_mib    = if ($vram -ne $null) { [int]($cfg.vram_total_mib - $vram) } else { $null }
    }
}

function Test-InferenceReady {   # LM Studio API on this machine: is the model loaded?
    try {
        $r = Invoke-RestMethod -Uri 'http://127.0.0.1:1234/api/v0/models' -TimeoutSec 3
        $m = @($r.data | Where-Object { $_.state -eq 'loaded' -and $_.type -ne 'embeddings' })
        return [pscustomobject]@{ up = $true; loaded = ($m.Count -gt 0); model = ($m | Select-Object -First 1 -ExpandProperty id); context = ($m | Select-Object -First 1 -ExpandProperty loaded_context_length) }
    } catch { return [pscustomobject]@{ up = $false; loaded = $false; model = $null; context = $null } }
}

function Get-LmsStatus($cfg) {   # 'IDLE' | 'BUSY' | 'NONE' ; text parse of `lms ps`
    try {
        $o = (& $cfg.lms_path ps 2>&1 | Out-String)
        if ($o -match 'No models are currently loaded') { return 'NONE' }
        if ($o -match '\bIDLE\b') { return 'IDLE' }
        return 'BUSY'
    } catch { return 'UNKNOWN' }
}

# ---------- reversible settings ----------
$script:PrevFile = Join-Path $script:Root 'previous.json'

function Save-PreviousSettings($cfg, $changedProcs) {
    $scheme = (powercfg /getactivescheme) -replace '.*GUID:\s*([0-9a-fA-F-]+).*', '$1'
    $obj = [pscustomobject]@{ saved = (Get-Date -Format o); power_scheme = $scheme.Trim(); processes = @($changedProcs) }
    $obj | ConvertTo-Json -Depth 4 | Set-Content -Path $script:PrevFile -Encoding UTF8
}

function Apply-AIPerformance($cfg) {
    $changed = @()
    $priMap = @{ 'AboveNormal' = 'AboveNormal'; 'BelowNormal' = 'BelowNormal' }   # Realtime/High are deliberately not available
    foreach ($pair in @(@($cfg.priority.raise, $cfg.priority.raise_to), @($cfg.priority.lower, $cfg.priority.lower_to))) {
        $to = $pair[1]; if (-not $priMap.ContainsKey($to)) { continue }
        foreach ($n in $pair[0]) {
            foreach ($p in (Get-Process -Name $n -ErrorAction SilentlyContinue)) {
                try {
                    $before = $p.PriorityClass.ToString()
                    # processes the app itself parked at Idle are left alone
                    if ($before -ne $to -and -not ($to -eq 'AboveNormal' -and $before -eq 'Idle')) { $p.PriorityClass = $to; $changed += [pscustomobject]@{ id = $p.Id; name = $p.ProcessName; start = $p.StartTime.ToString('o'); before = $before; after = $to } }
                } catch {}
            }
        }
    }
    $planNote = 'unchanged'
    $scheme = (powercfg /getactivescheme) -replace '.*GUID:\s*([0-9a-fA-F-]+).*', '$1'
    Save-PreviousSettings $cfg $changed          # saved BEFORE the plan switch so restore always knows the original
    if ($cfg.power_plan -and $cfg.power_plan -ne 'keep') {
        powercfg /setactive $cfg.power_plan | Out-Null
        $planNote = "switched to $($cfg.power_plan)"
    }
    Write-AILog "AI Mode performance applied: plan $planNote; priorities changed on $($changed.Count) process(es)"
    return $changed.Count
}

function Restore-AIPerformance {
    if (-not (Test-Path $script:PrevFile)) { return 'nothing to restore' }
    $prev = Get-Content $script:PrevFile -Raw | ConvertFrom-Json
    $restored = 0
    try { if ($prev.power_scheme) { powercfg /setactive $prev.power_scheme | Out-Null } } catch {}
    foreach ($e in @($prev.processes)) {
        try {
            $p = Get-Process -Id $e.id -ErrorAction Stop
            if ($p.ProcessName -eq $e.name -and $p.StartTime.ToString('o') -eq $e.start) { $p.PriorityClass = $e.before; $restored++ }   # same process only (PIDs get reused)
        } catch {}
    }
    Remove-Item $script:PrevFile -Force -ErrorAction SilentlyContinue
    Write-AILog "AI Mode performance restored: plan $($prev.power_scheme); priorities restored on $restored process(es)"
    return "restored plan + $restored process priorities"
}

# ---------- state file for server1 ----------
function Write-AIState($state) {
    $p = Join-Path $script:Root 'state.json'; $tmp = "$p.tmp"
    ($state | ConvertTo-Json -Depth 4) | Set-Content -Path $tmp -Encoding ASCII
    Move-Item $tmp $p -Force
}
