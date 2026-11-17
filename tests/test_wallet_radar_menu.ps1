# Mocked launcher test. No SSH, transfer, key prompt or surveillance is performed.
$ErrorActionPreference = 'Stop'
$menu = Join-Path (Split-Path $PSScriptRoot -Parent) 'windows/WALLET_RADAR.ps1'
$global:RadarCalls = [System.Collections.Generic.List[object]]::new()
$global:RadarScpCode = 0
function global:scp.exe {
    $global:RadarCalls.Add(@{kind='scp';values=@($args)})
    $global:LASTEXITCODE = $global:RadarScpCode
}
function global:ssh.exe {
    $global:RadarCalls.Add(@{kind='ssh';values=@($args)})
    $global:LASTEXITCODE = 0
}
function global:powershell.exe {
    $global:RadarCalls.Add(@{kind='start';values=@($args)})
    $global:LASTEXITCODE = 0
}
try {
    & $menu -ManageWallets
    if ($global:RadarCalls.Count -ne 2) { throw 'Manager must transfer scripts then start one SSH session' }
    $files = $global:RadarCalls[0].values
    if ($files.Count -ne 7 -or $files[0] -notlike '*configure_wallet_alerts.py' -or $files[1] -notlike '*wallet_alerts_ws.py' -or $files[2] -notlike '*wallet_radar_paper.py' -or $files[3] -notlike '*wallet_radar_diagnostics.py' -or $files[4] -notlike '*wallet_radar_live.py' -or $files[5] -notlike '*requirements-wallet-radar-live.txt') {
        throw 'Unexpected transfer source files'
    }
    if ($global:RadarCalls[1].values[-1] -cne '/home/bot/wallet-alerts/.venv/bin/python /home/bot/wallet-alerts/scripts/configure_wallet_alerts.py --manage-wallets') {
        throw 'Unexpected remote command'
    }
    $global:RadarCalls.Clear(); $global:RadarScpCode = 1
    & $menu -ManageWallets
    if ($LASTEXITCODE -ne 1 -or $global:RadarCalls.Count -ne 1) { throw 'Failed transfer must not start remote editor' }

    $global:RadarCalls.Clear(); $global:RadarScpCode = 0
    $global:RadarAnswers = [System.Collections.Generic.Queue[string]]::new()
    foreach ($answer in @('2','1','0')) { $global:RadarAnswers.Enqueue($answer) }
    function global:Read-Host { return $global:RadarAnswers.Dequeue() }
    & $menu
    if ($global:RadarCalls.Count -ne 4 -or $global:RadarCalls[2].kind -cne 'scp' -or $global:RadarCalls[3].kind -cne 'start') { throw 'Menu must update server scripts before the normal start' }
    if ($global:RadarCalls[3].values[-1] -cne '-SingleTab') { throw 'Surveillance must keep single-tab mode' }
    $global:RadarCalls.Clear()
    foreach ($answer in @('3','0')) { $global:RadarAnswers.Enqueue($answer) }
    & $menu
    if ($global:RadarCalls.Count -ne 2 -or $global:RadarCalls[0].kind -cne 'scp' -or $global:RadarCalls[1].kind -cne 'start') { throw 'Simulation must transfer scripts before starting' }
    if ($global:RadarCalls[1].values[-1] -cne '-PaperBuys') { throw 'Simulation flag missing' }
    $global:RadarCalls.Clear()
    foreach ($answer in @('4','0')) { $global:RadarAnswers.Enqueue($answer) }
    & $menu
    if ($global:RadarCalls.Count -ne 1 -or $global:RadarCalls[0].values[-1] -notlike '*--paper-report') { throw 'Offline report command missing' }
    $global:RadarCalls.Clear()
    foreach ($answer in @('5','0')) { $global:RadarAnswers.Enqueue($answer) }
    & $menu
    if ($global:RadarCalls.Count -ne 2 -or $global:RadarCalls[0].kind -cne 'scp' -or $global:RadarCalls[1].values[-1] -notlike '*--stop') { throw 'Stop must update the helper then call its scoped stop command' }
    $global:RadarCalls.Clear()
    foreach ($answer in @('6','0')) { $global:RadarAnswers.Enqueue($answer) }
    & $menu
    if ($global:RadarCalls.Count -ne 2 -or $global:RadarCalls[0].kind -cne 'scp' -or $global:RadarCalls[1].values[-1] -notlike '*--paper-diagnose') { throw 'Read-only diagnostics dispatch missing' }
    $global:RadarCalls.Clear()
    foreach ($answer in @('7','8','9','10','11','12','0')) { $global:RadarAnswers.Enqueue($answer) }
    & $menu
    if ($global:RadarCalls.Count -ne 11 -or $global:RadarCalls[1].values[-1] -notlike '*--setup-if-missing*--check' -or $global:RadarCalls[3].values[-1] -cne '-LiveBuys' -or $global:RadarCalls[4].values[-1] -notlike '*--report' -or $global:RadarCalls[6].values[-1] -notlike '*--check' -or $global:RadarCalls[8].values[-1] -notlike '*--update-jupiter*--check' -or $global:RadarCalls[10].values[-1] -notlike '*--diagnose-exit') { throw 'Real setup/start/report/check/update/exit diagnostic dispatch missing' }
    Write-Host 'Wallet Radar Windows menu tests OK; all external actions mocked.'
} finally {
    Remove-Item Function:\scp.exe,Function:\ssh.exe,Function:\powershell.exe,Function:\Read-Host -ErrorAction SilentlyContinue
}
