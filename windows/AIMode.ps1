# AI Mode tray app. Runs in the owner's interactive session. Default state at every start: OFF (never auto-ON after a reboot or outage).
# Tray icon: grey = OFF, green = ON, orange = GAMING. Left-click = toggle AI Mode. Right-click = menu.
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'AIModeLib.ps1')

$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Global\AIModeTray', [ref]$created)
if (-not $created) { exit }          # single instance

$cfg = Get-AIConfig
$ctl = Join-Path $script:Root 'control'; New-Item -ItemType Directory -Force -Path $ctl | Out-Null
$script:mode = 'OFF'; $script:gaming = $false; $script:game = $null; $script:inf = $null; $script:metrics = $null
$script:unloadPending = $false; $script:unloadSince = $null; $script:idleStreak = 0; $script:modelGameUnloaded = $false
$script:lastNote = 'started'; $script:started = Get-Date
$script:loadAttempts = 0; $script:lastLoadAttempt = $null; $script:autoLoaded = $false; $script:loadingSince = $null

# a crash/reboot while ON can leave changed settings behind: restore first, always
if (Test-Path (Join-Path $script:Root 'previous.json')) { [void](Restore-AIPerformance); Write-AILog 'restored settings left over from a previous run' }
Write-AILog 'AI Mode tray started (mode OFF)'

function New-Icon($color) {
    $bmp = New-Object System.Drawing.Bitmap 32, 32
    $g = [System.Drawing.Graphics]::FromImage($bmp); $g.SmoothingMode = 'AntiAlias'
    $g.FillEllipse((New-Object System.Drawing.SolidBrush $color), 2, 2, 28, 28)
    $f = New-Object System.Drawing.Font 'Segoe UI', 12, ([System.Drawing.FontStyle]::Bold)
    $g.DrawString('AI', $f, [System.Drawing.Brushes]::White, 3, 6); $g.Dispose()
    [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
}
$icons = @{ OFF = New-Icon ([System.Drawing.Color]::FromArgb(120,120,120)); ON = New-Icon ([System.Drawing.Color]::FromArgb(40,160,70)); GAMING = New-Icon ([System.Drawing.Color]::FromArgb(230,140,20)) }

$tray = New-Object System.Windows.Forms.NotifyIcon
$tray.Visible = $true
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$miToggle = $menu.Items.Add('Turn AI Mode ON')
$miLoad   = $menu.Items.Add('Load model (Mellum 16K, 1 slot)...')
$miUnload = $menu.Items.Add('Unload model (after current request)')
$miStatus = $menu.Items.Add('Show status')
$miLog    = $menu.Items.Add('Open log')
[void]$menu.Items.Add('-')
$miExit   = $menu.Items.Add('Exit (restores settings)')
$tray.ContextMenuStrip = $menu

function Notify($title, $text) { $tray.BalloonTipTitle = $title; $tray.BalloonTipText = $text; $tray.ShowBalloonTip(4000) }

function Refresh-Ui {
    $label = if ($script:gaming) { 'GAMING' } else { $script:mode }
    $tray.Icon = $icons[$label]
    $miToggle.Text = if ($script:mode -eq 'ON') { 'Turn AI Mode OFF' } else { 'Turn AI Mode ON' }
    $model = if ($script:inf -and $script:inf.loaded) { 'model loaded' } else { 'no model loaded' }
    $ram = if ($script:metrics) { "RAM free $($script:metrics.ram_available_mb) MB" } else { '' }
    $txt = switch ($label) { 'ON' { "AI Mode ON - $model - $ram" } 'GAMING' { "GAMING ($($script:game)) - AI paused, AI Mode OFF" } default { "AI Mode OFF - $model" } }
    $tray.Text = $txt.Substring(0, [math]::Min(63, $txt.Length))
}

function Publish-State {
    $inf = $script:inf; $m = $script:metrics
    Write-AIState ([ordered]@{
        ts = [int][double]::Parse((Get-Date -UFormat %s)); ai_mode = $script:mode; gaming = $script:gaming; game = $script:game
        logged_in = $true; lmstudio_up = [bool]($inf -and $inf.up); model_loaded = [bool]($inf -and $inf.loaded); model = if ($inf) { $inf.model } else { $null }
        context = if ($inf) { $inf.context } else { $null }
        ram_available_mb = if ($m) { $m.ram_available_mb } else { $null }; commit_pct = if ($m) { $m.commit_pct } else { $null }
        vram_used_mib = if ($m) { $m.vram_used_mib } else { $null }; vram_free_mib = if ($m) { $m.vram_free_mib } else { $null }
        unload_pending = $script:unloadPending; note = $script:lastNote; app_uptime_s = [int]((Get-Date) - $script:started).TotalSeconds
        power_plan = ((powercfg /getactivescheme) -replace '.*\((.*)\).*', '$1')
    })
}

function Set-Mode($want, $why) {
    if ($want -eq $script:mode) { return }
    if ($want -eq 'ON') {
        if ($script:gaming) { $script:lastNote = 'refused: a game is running'; Notify 'AI Mode' 'A game is running - AI Mode stays OFF.'; return }
        $n = Apply-AIPerformance $cfg
        $script:mode = 'ON'; $script:lastNote = "ON ($why)"; Write-AILog "AI Mode ON ($why)"
        Notify 'AI Mode ON' 'Unattended AI work may run 03:00-09:00 if everything is safe.'
        # ON loads the model (no dialog: pressing ON is the approval). Refused quietly if RAM is too low or a game is running.
        if ($cfg.on_loads_model) {
            $i = Test-InferenceReady; $m = Get-AIMetrics $cfg
            if ($i.loaded) { Write-AILog 'AI Mode ON: model already loaded' }
            elseif ($m.ram_available_mb -lt $cfg.load_min_available_mb) { $script:lastNote = "ON, model NOT loaded: only $($m.ram_available_mb) MB RAM free (need $($cfg.load_min_available_mb))"; Write-AILog $script:lastNote; Notify 'AI Mode ON' 'Not enough free RAM to load the model. Close something and press ON again.' }
            else { Do-LoadSilent "AI Mode ON ($why)"; $script:lastNote = "ON ($why); loading model" }
        }
    } else {
        [void](Restore-AIPerformance)
        $script:mode = 'OFF'; $script:lastNote = "OFF ($why)"; Write-AILog "AI Mode OFF ($why)"
        Notify 'AI Mode OFF' 'AI workload prioritisation disabled. Any request in progress will finish; no new unattended work.'
        # OFF = the PC is yours: unload the model once the current request finishes (Discord etc. are never touched)
        if ($cfg.off_unloads_model -and $why -ne 'exit') { Request-Unload "AI Mode OFF ($why)"; $script:lastNote = "OFF ($why); unloading model when idle" }
    }
    Refresh-Ui; Publish-State
}

function Request-Unload($why) {
    if ($script:unloadPending) { return }
    $script:unloadPending = $true; $script:unloadSince = Get-Date; $script:idleStreak = 0
    Write-AILog "unload requested ($why); will wait for the current request to finish"
}

function Do-Unload { try { & $cfg.lms_path unload --all 2>&1 | Out-Null; Write-AILog 'model unloaded' } catch { Write-AILog "unload failed: $($_.Exception.Message)" } }

function Do-LoadSilent($why) {
    Write-AILog "model load started ($why)"
    Start-Process -WindowStyle Hidden -FilePath $cfg.lms_path -ArgumentList @('load', $cfg.model.key, '--gpu', 'max', '-c', $cfg.model.context, '--parallel', $cfg.model.parallel, '--identifier', $cfg.model.key, '-y')
    $script:modelGameUnloaded = $false
}

function Do-Load {
    if ($script:gaming) { [void][System.Windows.Forms.MessageBox]::Show("A game is running ($($script:game)). Not loading the model while gaming.", 'AI Mode'); return }
    $m = Get-AIMetrics $cfg
    if ($m.ram_available_mb -lt $cfg.load_min_available_mb) {
        [void][System.Windows.Forms.MessageBox]::Show("Only $($m.ram_available_mb) MB of RAM is free (need $($cfg.load_min_available_mb) MB). Close something first.", 'AI Mode - load refused'); return }
    $a = [System.Windows.Forms.MessageBox]::Show("Load $($cfg.model.key) at $($cfg.model.context) context, 1 slot?`n`nIt will use about 7.4 GB VRAM and up to 15 GB of committed RAM.", 'AI Mode - load model', 'YesNo')
    if ($a -ne 'Yes') { return }
    Write-AILog 'model load started by the owner'
    Start-Process -WindowStyle Hidden -FilePath $cfg.lms_path -ArgumentList @('load', $cfg.model.key, '--gpu', 'max', '-c', $cfg.model.context, '--parallel', $cfg.model.parallel, '--identifier', $cfg.model.key, '-y')
    $script:modelGameUnloaded = $false
}

$miToggle.add_Click({ Set-Mode ($(if ($script:mode -eq 'ON') { 'OFF' } else { 'ON' })) 'tray menu' })
$tray.add_MouseClick({ param($s, $e) if ($e.Button -eq 'Left') { Set-Mode ($(if ($script:mode -eq 'ON') { 'OFF' } else { 'ON' })) 'tray click' } })
$miLoad.add_Click({ Do-Load })
$miUnload.add_Click({ Request-Unload 'tray menu' })
$miStatus.add_Click({ [void][System.Windows.Forms.MessageBox]::Show(((Get-Content (Join-Path $script:Root 'state.json') -Raw)), 'AI Mode status') })
$miLog.add_Click({ Start-Process notepad.exe (Join-Path $script:Root 'aimode.log') })
$miExit.add_Click({ if ($script:mode -eq 'ON') { Set-Mode 'OFF' 'exit' }; $tray.Visible = $false; $timer.Stop(); [System.Windows.Forms.Application]::Exit() })

# ----- control channel for the CLI (tests / scripting): control\cmd.txt contains one command -----
function Handle-Command($c) {
    switch -Regex ($c.Trim()) {
        '^on$'          { Set-Mode 'ON' 'cli' }
        '^off$'         { Set-Mode 'OFF' 'cli' }
        '^unload$'      { Request-Unload 'cli' }
        '^simulate-game (.+)$' { $script:simGame = $Matches[1]; Write-AILog "SIMULATED game '$($Matches[1])' (test)" }
        '^end-simulated-game$' { $script:simGame = $null; Write-AILog 'simulated game ended (test)' }
    }
}

$script:simGame = $null; $script:tick = 0
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.add_Tick({
    $script:tick++
    $cmdFile = Join-Path $ctl 'cmd.txt'
    if (Test-Path $cmdFile) { $c = Get-Content $cmdFile -Raw; Remove-Item $cmdFile -Force; Handle-Command $c }
    # gaming check
    if (($script:tick % [math]::Max(1, [int]($cfg.game_check_interval_s / 5))) -eq 0 -and $cfg.gaming.enabled) {
        $g = if ($script:simGame) { $script:simGame } else { Find-RunningGame $cfg }
        if ($g -and -not $script:gaming) {
            $script:gaming = $true; $script:game = $g; Write-AILog "GAMING detected: $g"
            if ($script:mode -eq 'ON') { Set-Mode 'OFF' "gaming: $g" }
            if ($cfg.gaming.unload_model) { Request-Unload "gaming: $g"; $script:modelGameUnloaded = $true }
            Notify 'Gaming mode' "$g detected - pausing AI. AI Mode is OFF; the model will unload once idle."
        } elseif (-not $g -and $script:gaming) {
            $script:gaming = $false; $script:game = $null; Write-AILog 'game closed - AI Mode stays OFF; model NOT reloaded (the owner decides)'
            $script:lastNote = 'game closed; AI Mode OFF; model not reloaded'
            Notify 'Game closed' 'AI Mode stays OFF and the model is not reloaded. Turn AI Mode on when you want it.'
        }
    }
    # pending unload: wait until LM Studio reports IDLE twice in a row, never mid-request
    if ($script:unloadPending) {
        $st = Get-LmsStatus $cfg
        if ($st -eq 'NONE') { $script:unloadPending = $false; Write-AILog 'unload: nothing loaded' }
        elseif ($st -eq 'IDLE') { $script:idleStreak++ } else { $script:idleStreak = 0 }
        if ($script:unloadPending -and $script:idleStreak -ge 2) { Do-Unload; $script:unloadPending = $false }
        elseif ($script:unloadPending -and ((Get-Date) - $script:unloadSince).TotalSeconds -gt $cfg.gaming.unload_wait_idle_max_s) {
            Write-AILog 'unload gave up waiting for idle (request still running); leaving the model loaded'; $script:unloadPending = $false; $script:lastNote = 'unload skipped: model still busy' }
    }
    # metrics + state every state_interval_s
    if (($script:tick % [math]::Max(1, [int]($cfg.state_interval_s / 5))) -eq 0) {
        $script:inf = Test-InferenceReady; $script:metrics = Get-AIMetrics $cfg
        if ($script:mode -eq 'ON' -and $script:metrics.ram_available_mb -lt $cfg.ram_unsafe_available_mb) { $script:lastNote = "WARNING low RAM: $($script:metrics.ram_available_mb) MB" }
        # night cycle
        $now = Get-Date
        $why = Test-PreloadDue $now $cfg $script:mode $script:gaming $script:inf $script:metrics $script:loadAttempts $script:lastLoadAttempt
        if ($why) {
            $script:loadAttempts++; $script:lastLoadAttempt = $now; $script:autoLoaded = $true
            Do-LoadSilent "$why, attempt $($script:loadAttempts)"; $script:lastNote = "loading model for the night (attempt $($script:loadAttempts))"
            Notify 'AI Mode' 'Loading Mellum for the night window.'
        }
        if ((Get-NightPhase $now $cfg) -eq 'outside' -and $now.Hour -ge 12) { $script:loadAttempts = 0 }       # new evening: allow tomorrow's attempts
        if (Test-NightEndDue $now $cfg $script:mode $script:autoLoaded) {
            Write-AILog 'night window finished: handing the PC back (unload when idle, AI Mode OFF)'
            $script:autoLoaded = $false; Request-Unload 'night window finished'; Set-Mode 'OFF' 'night window finished'
        }
        Refresh-Ui; Publish-State
    }
})
$script:inf = Test-InferenceReady; $script:metrics = Get-AIMetrics $cfg; Refresh-Ui; Publish-State
$timer.Start()
[System.Windows.Forms.Application]::Run()
$tray.Dispose(); $mutex.ReleaseMutex()
