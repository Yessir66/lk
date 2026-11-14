# Run with PowerShell on any platform. All external actions are mocked.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$temp = Join-Path ([IO.Path]::GetTempPath()) ('gmgn-launcher-test-' + [guid]::NewGuid())
$names = @('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA')
$original = @{}
foreach ($name in $names) { $original[$name] = [Environment]::GetEnvironmentVariable($name,'Process') }
try {
    [void](New-Item -ItemType Directory -Path (Join-Path $temp 'Google/Chrome/Application') -Force)
    [void](New-Item -ItemType File -Path (Join-Path $temp 'Google/Chrome/Application/chrome.exe'))
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name,$temp,'Process') }
    $global:GmgnOpened = [System.Collections.Generic.List[string]]::new()
    function global:Start-Process {
        param($FilePath,$ArgumentList)
        $global:GmgnOpened.Add([string]$ArgumentList[1])
    }
    function global:ssh.exe {
        $global:LASTEXITCODE = 0
        'WEBSOCKET CONNECTE (mock)'
        $item = @{v=1;type='verified_buy';mint=('A'*32);wallet=('B'*32);signature=('C'*88);detected_at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()}
        'GMGN_BUY ' + ($item | ConvertTo-Json -Compress)
        'GMGN_BUY ' + ($item | ConvertTo-Json -Compress)
        $item.signature = 'D'*88
        'GMGN_BUY ' + ($item | ConvertTo-Json -Compress)
        $item.type = 'sell'; $item.signature = 'E'*88
        'GMGN_BUY ' + ($item | ConvertTo-Json -Compress)
        'TRANSACTION PROVISOIRE (mock)'
    }
    & (Join-Path $root 'windows/OUVRIR_CHARTS_GMGN.ps1')
    if ($global:GmgnOpened.Count -ne 3) { throw 'Expected one warmup and two distinct buys' }
    if ($global:GmgnOpened[1] -cne ('https://gmgn.ai/sol/token/' + ('A'*32))) { throw 'Incorrect fixed GMGN URL' }
    & (Join-Path $root 'windows/OUVRIR_CHARTS_GMGN.ps1')
    if ($global:GmgnOpened.Count -ne 4) { throw 'Restart duplicated a previously opened trade' }
    $state = Join-Path $temp 'WalletAlertsGMGN'
    Remove-Item -LiteralPath (Join-Path $state 'opened-buys.txt')
    [void](New-Item -ItemType Directory -Force -Path (Join-Path $state 'nativehost'))
    [IO.File]::WriteAllText((Join-Path $state 'nativehost/fr.walletalerts.gmgn.json'),'{}')
    & (Join-Path $root 'windows/OUVRIR_CHARTS_GMGN.ps1') -SingleTab
    if ($global:GmgnOpened.Count -ne 4) { throw 'Single-tab mode must never launch a new Chrome tab' }
    $session = [IO.File]::ReadAllText((Join-Path $state 'bridge-session.txt')).Trim()
    $events = @(Get-Content -LiteralPath (Join-Path $state ('gmgn-events-' + $session + '.jsonl')))
    if ($events.Count -ne 2) { throw 'Expected exactly two distinct public buy events in bridge journal' }
    foreach ($line in $events) { if (($line | ConvertFrom-Json).type -cne 'verified_buy') { throw 'Non-buy entered bridge' } }
    Write-Host 'Launcher integration OK: recent buys, duplicate suppression and persistent restart tested with mocks.'
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name,$original[$name],'Process') }
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item Function:\Start-Process,Function:\ssh.exe -ErrorAction SilentlyContinue
}
