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
    if ($files.Count -ne 3 -or $files[0] -notlike '*configure_wallet_alerts.py' -or $files[1] -notlike '*wallet_alerts_ws.py') {
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
    if ($global:RadarCalls.Count -ne 3 -or $global:RadarCalls[2].kind -cne 'start') { throw 'Menu did not dispatch manage and start' }
    if ($global:RadarCalls[2].values[-1] -cne '-SingleTab') { throw 'Surveillance must keep single-tab mode' }
    Write-Host 'Wallet Radar Windows menu tests OK; all external actions mocked.'
} finally {
    Remove-Item Function:\scp.exe,Function:\ssh.exe,Function:\powershell.exe,Function:\Read-Host -ErrorAction SilentlyContinue
}
