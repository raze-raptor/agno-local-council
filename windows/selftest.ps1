. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'AIModeLib.ps1')
$cfg = Get-AIConfig; $pass = 0; $fail = 0
function T($name, $got, $want) { if ($got -eq $want) { $script:pass++; "PASS $name" } else { $script:fail++; "FAIL $name  got='$got' want='$want'" } }
$mc = 'javaw.exe -Xmx4G -Djava.library.path=natives -cp libs\a.jar net.minecraft.client.main.Main --username x --gameDir C:\Users\YOU\AppData\Roaming\.minecraft --assetIndex 12'
T 'Lunar Client launcher open = not gaming'      (Test-GameProcess 'Lunar Client.exe' 'C:\Users\YOU\AppData\Local\Programs\Lunar Client\Lunar Client.exe' '' $cfg) $null
T 'Modrinth App launcher open = not gaming'      (Test-GameProcess 'Modrinth App.exe' 'C:\Program Files\Modrinth App\Modrinth App.exe' '' $cfg) $null
T 'Modrinth helper theseus_gui = not gaming'     (Test-GameProcess 'theseus_gui.exe' '' '' $cfg) $null
T 'Minecraft launcher open = not gaming'         (Test-GameProcess 'MinecraftLauncher.exe' 'C:\Program Files (x86)\Minecraft Launcher\MinecraftLauncher.exe' '' $cfg) $null
T 'javaw running vanilla Minecraft = gaming'     (Test-GameProcess 'javaw.exe' 'C:\x\javaw.exe' $mc $cfg) 'minecraft-java'
T 'javaw running Fabric Minecraft = gaming'      (Test-GameProcess 'javaw.exe' 'C:\x\javaw.exe' 'javaw -cp x net.fabricmc.loader.impl.launch.knot.KnotClient --gameDir g' $cfg) 'minecraft-java'
T 'javaw running Lunar-launched game = gaming'   (Test-GameProcess 'javaw.exe' 'C:\x\javaw.exe' 'javaw -cp x com.moonsworth.lunar.genesis.Main --gameDir g' $cfg) 'minecraft-java'
T 'java running IntelliJ/Gradle = not gaming'    (Test-GameProcess 'java.exe' 'C:\x\java.exe' 'java -classpath gradle-launcher.jar org.gradle.launcher.daemon.bootstrap.GradleDaemon' $cfg) $null
T 'java with no command line = not gaming'       (Test-GameProcess 'java.exe' 'C:\x\java.exe' '' $cfg) $null
T 'Bedrock Minecraft.Windows = gaming'           (Test-GameProcess 'Minecraft.Windows.exe' 'C:\Program Files\WindowsApps\Microsoft.MinecraftUWP\Minecraft.Windows.exe' '' $cfg) 'Minecraft.Windows.exe'
T 'Roblox Player = gaming'                       (Test-GameProcess 'RobloxPlayerBeta.exe' 'C:\Users\YOU\AppData\Local\Roblox\Versions\version-1\RobloxPlayerBeta.exe' '' $cfg) 'RobloxPlayerBeta.exe'
T 'Roblox STUDIO = NOT gaming'                   (Test-GameProcess 'RobloxStudioBeta.exe' 'C:\Users\YOU\AppData\Local\Roblox\Versions\version-2\RobloxStudioBeta.exe' '' $cfg) $null
T 'Steam game (Phasmophobia)'                    (Test-GameProcess 'Phasmophobia.exe' 'C:\Program Files (x86)\Steam\steamapps\common\Phasmophobia\Phasmophobia.exe' '' $cfg) 'Phasmophobia.exe'
T 'Steam game on D: library (Aim Lab)'           (Test-GameProcess 'AimLab_tb.exe' 'D:\SteamLibrary\steamapps\common\Aim Lab\AimLab_tb.exe' '' $cfg) 'AimLab_tb.exe'
T 'Steam redistributable installer = not gaming' (Test-GameProcess 'vcredist_x64.exe' 'C:\Program Files (x86)\Steam\steamapps\common\Steamworks Shared\_CommonRedist\vcredist\vcredist_x64.exe' '' $cfg) $null
T 'steamwebhelper = not gaming'                  (Test-GameProcess 'steamwebhelper.exe' 'C:\Program Files (x86)\Steam\bin\cef\steamwebhelper.exe' '' $cfg) $null
T 'Riot Client launcher = not gaming'            (Test-GameProcess 'RiotClientServices.exe' 'C:\Riot Games\Riot Client\RiotClientServices.exe' '' $cfg) $null
T 'VALORANT game process = gaming'               (Test-GameProcess 'VALORANT-Win64-Shipping.exe' 'C:\Riot Games\VALORANT\live\ShooterGame\Binaries\Win64\VALORANT-Win64-Shipping.exe' '' $cfg) 'VALORANT-Win64-Shipping.exe'
T 'C:\Games title'                               (Test-GameProcess 'OPM.exe' 'C:\Games\ONE.PUNCH.MAN\OPM\Binaries\Win64\OPM.exe' '' $cfg) 'OPM.exe'
T 'Discord = not gaming'                         (Test-GameProcess 'Discord.exe' 'C:\Users\YOU\AppData\Local\Discord\app-1\Discord.exe' '' $cfg) $null
T 'Brave = not gaming'                           (Test-GameProcess 'brave.exe' 'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe' '' $cfg) $null
T 'Medal recorder = not gaming'                  (Test-GameProcess 'Medal.exe' 'C:\Users\YOU\AppData\Local\Medal\Medal.exe' '' $cfg) $null
T 'LM Studio = not gaming'                       (Test-GameProcess 'LM Studio.exe' 'C:\Users\YOU\AppData\Local\Programs\LM Studio\LM Studio.exe' '' $cfg) $null
"--- live scan of this PC right now (expect none unless a game is really running)"
"running game label: [" + (Find-RunningGame $cfg) + "]"
"--- apply/restore cycle on a harmless dummy process"
$dummy = Start-Process timeout.exe -ArgumentList '/t 90 /nobreak' -PassThru -WindowStyle Hidden; Start-Sleep -Milliseconds 800
$before = (Get-Process -Id $dummy.Id).PriorityClass.ToString(); $plan0 = (powercfg /getactivescheme)
$cfg2 = $cfg.PSObject.Copy(); $cfg2.priority = [pscustomobject]@{ raise = @('timeout'); raise_to = 'AboveNormal'; lower = @(); lower_to = 'BelowNormal' }
$n = Apply-AIPerformance $cfg2
T 'dummy raised to AboveNormal' (Get-Process -Id $dummy.Id).PriorityClass.ToString() 'AboveNormal'
T 'previous.json written' (Test-Path (Join-Path $script:Root 'previous.json')) $true
$msg = Restore-AIPerformance
T 'dummy priority restored' (Get-Process -Id $dummy.Id).PriorityClass.ToString() $before
T 'power plan unchanged (keep)' ((powercfg /getactivescheme) -eq $plan0) $true
T 'previous.json removed' (Test-Path (Join-Path $script:Root 'previous.json')) $false
Stop-Process -Id $dummy.Id -Force -ErrorAction SilentlyContinue
"--- power plan switch + restore (Ultimate Performance, then back)"
$cfg3 = $cfg.PSObject.Copy(); $cfg3.power_plan = '180322c8-9e29-44e4-b75a-14c5ccc3f97f'; $cfg3.priority = [pscustomobject]@{ raise = @(); raise_to = 'AboveNormal'; lower = @(); lower_to = 'BelowNormal' }
Apply-AIPerformance $cfg3 | Out-Null
T 'Ultimate Performance active while ON' ((powercfg /getactivescheme) -match 'Ultimate Performance') $true
Restore-AIPerformance | Out-Null
T 'original plan active after OFF' ((powercfg /getactivescheme) -eq $plan0) $true
"RESULT: $pass passed, $fail failed"
