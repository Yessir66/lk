param([switch]$ManageWallets)
$ErrorActionPreference = 'Stop'
if ($env:OS -eq 'Windows_NT') {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
}
$projectRoot = Split-Path $PSScriptRoot -Parent

function Invoke-WalletManager {
    if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue) -or -not (Get-Command scp.exe -ErrorAction SilentlyContinue)) {
        throw 'OpenSSH Windows introuvable.'
    }
    $sources = @(
        (Join-Path $projectRoot 'scripts\configure_wallet_alerts.py'),
        (Join-Path $projectRoot 'scripts\wallet_alerts_ws.py')
    )
    foreach ($source in $sources) {
        if (-not (Test-Path -LiteralPath $source)) { throw ('Fichier manquant : ' + $source) }
    }
    Write-Host 'WALLET RADAR - Gestion des wallets' -ForegroundColor Green
    Write-Host 'Si la surveillance tourne, arrete-la avec Ctrl+C dans son autre fenetre.'
    Write-Host 'Connexion au VPS. Le mot de passe habituel peut etre demande deux fois.'
    Write-Host 'Les cles API et les reglages GMGN sont conserves.'
    & scp.exe @sources bot@2.24.62.69:/home/bot/wallet-alerts/scripts/
    if ($LASTEXITCODE -ne 0) { throw 'Transfert interrompu. Aucune gestion lancee.' }
    $remote = '/home/bot/wallet-alerts/.venv/bin/python /home/bot/wallet-alerts/scripts/configure_wallet_alerts.py --manage-wallets'
    & ssh.exe -t -o ServerAliveInterval=15 -o ServerAliveCountMax=3 bot@2.24.62.69 $remote
    if ($LASTEXITCODE -ne 0) { throw 'Connexion terminee avec une erreur. Verifie les messages ci-dessus.' }
}

try {
    if ($ManageWallets) { Invoke-WalletManager; exit 0 }
    while ($true) {
        Write-Host ''
        Write-Host 'WALLET RADAR' -ForegroundColor Green
        Write-Host '1 - Demarrer la surveillance et GMGN en onglet unique'
        Write-Host '2 - Ajouter, retirer ou renommer les wallets'
        Write-Host '0 - Quitter'
        $choice = Read-Host 'Ton choix'
        if ($choice -eq '0') { break }
        try {
            if ($choice -eq '1') {
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'OUVRIR_CHARTS_GMGN.ps1') -SingleTab
            } elseif ($choice -eq '2') {
                Invoke-WalletManager
            } else { Write-Host 'Choisis 0, 1 ou 2.' }
        } catch { Write-Host $_.Exception.Message -ForegroundColor Red }
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
