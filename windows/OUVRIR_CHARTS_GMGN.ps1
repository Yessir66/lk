param(
    [switch]$Test
)

$ErrorActionPreference = 'Stop'
$prefix = 'GMGN_BUY '

function Get-GmgnBuy {
    param([string]$Line)
    if (-not $Line.StartsWith($prefix)) { return $null }
    try { $event = $Line.Substring($prefix.Length) | ConvertFrom-Json } catch { return $null }
    if ($event.v -ne 1 -or $event.type -cne 'verified_buy') { return $null }
    if ([string]$event.mint -cnotmatch '^[1-9A-HJ-NP-Za-km-z]{32,44}$') { return $null }
    if ([string]$event.wallet -cnotmatch '^[1-9A-HJ-NP-Za-km-z]{32,44}$') { return $null }
    if ([string]$event.signature -cnotmatch '^[1-9A-HJ-NP-Za-km-z]{64,100}$') { return $null }
    return $event
}

if ($Test) {
    # Pure protocol checks: no SSH, Chrome, keys, orders or Telegram.
    $sample = @{v=1;type='verified_buy';mint=('A'*32);wallet=('B'*32);signature=('C'*88)}
    if ($null -eq (Get-GmgnBuy ($prefix + ($sample | ConvertTo-Json -Compress)))) { throw 'Valid buy rejected' }
    foreach ($field in @('mint','wallet','signature')) {
        $bad = $sample.Clone(); $bad[$field] = 'https://other.example/?command=bad'
        if ($null -ne (Get-GmgnBuy ($prefix + ($bad | ConvertTo-Json -Compress)))) { throw 'Invalid identifier accepted' }
    }
    $sample.type = 'sell'
    if ($null -ne (Get-GmgnBuy ($prefix + ($sample | ConvertTo-Json -Compress)))) { throw 'Sell accepted' }
    if ($null -ne (Get-GmgnBuy 'GMGN_BUY invalid-json')) { throw 'Invalid JSON accepted' }
    if ($null -ne (Get-GmgnBuy 'TRANSACTION PROVISOIRE')) { throw 'Provisional accepted' }
    Write-Host 'Protocol tests OK; no network or browser opened.'
    exit 0
}

try {
    if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) { throw 'Client OpenSSH Windows introuvable.' }
    $candidates = @(
        "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    $chrome = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $chrome) { throw 'Google Chrome introuvable dans les dossiers Windows habituels.' }

    $localState = Join-Path $env:LOCALAPPDATA 'WalletAlertsGMGN'
    New-Item -ItemType Directory -Force -Path $localState | Out-Null
    $seenFile = Join-Path $localState 'opened-buys.txt'
    $seen = New-Object 'System.Collections.Generic.HashSet[string]'
    if (Test-Path -LiteralPath $seenFile) {
        Get-Content -LiteralPath $seenFile | ForEach-Object { [void]$seen.Add($_) }
    }

    Write-Host 'GMGN : un nouvel onglet par achat verifie recent. Ventes manuelles.'
    Write-Host 'Connexion au VPS : utilise le mot de passe habituel si SSH le demande.'
    Write-Host 'Laisse cette fenetre ouverte. Ctrl+C pour arreter.'
    Start-Process -FilePath $chrome -ArgumentList @('--new-tab','https://gmgn.ai/sol')

    # Fixed remote command, no credentials on the command line; no exposed web server.
    $remote = 'exec /home/bot/wallet-alerts/.venv/bin/python -u /home/bot/wallet-alerts/scripts/wallet_alerts_ws.py --early --browser-events'
    & ssh.exe -T -o ServerAliveInterval=15 -o ServerAliveCountMax=3 bot@2.24.62.69 $remote | ForEach-Object {
        $line = [string]$_
        $event = Get-GmgnBuy $line
        if ($null -eq $event) {
            if (-not $line.StartsWith($prefix)) { Write-Host $line }
        } else {
            $key = [string]$event.signature + '|' + [string]$event.mint
            if (-not $seen.Contains($key)) {
                # Only a validated CA enters the fixed GMGN URL; never execute remote text.
                $url = 'https://gmgn.ai/sol/token/' + [string]$event.mint
                try {
                    Start-Process -FilePath $chrome -ArgumentList @('--new-tab',$url)
                    Add-Content -LiteralPath $seenFile -Value $key -Encoding ASCII
                    [void]$seen.Add($key)
                    Write-Host ('CHART GMGN OUVERT : ' + [string]$event.mint) -ForegroundColor Green
                } catch { Write-Warning ('Ouverture impossible. Lien : ' + $url) }
            }
        }
    }
    if ($LASTEXITCODE -ne 0) { throw ('Connexion SSH terminee avec le code ' + $LASTEXITCODE + '. Verifie la connexion et les messages ci-dessus.') }
    Write-Host 'Surveillance terminee.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
