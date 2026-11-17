param([switch]$ManageWallets)
$ErrorActionPreference = 'Stop'
if ($env:OS -eq 'Windows_NT') {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
}
$projectRoot = Split-Path $PSScriptRoot -Parent

function Send-RadarScripts {
    param([switch]$AllowRunning)
    if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue) -or -not (Get-Command scp.exe -ErrorAction SilentlyContinue)) {
        throw 'OpenSSH Windows introuvable.'
    }
    $sources = @(
        (Join-Path $projectRoot 'scripts\configure_wallet_alerts.py'),
        (Join-Path $projectRoot 'scripts\wallet_alerts_ws.py'),
        (Join-Path $projectRoot 'scripts\wallet_radar_paper.py'),
        (Join-Path $projectRoot 'scripts\wallet_radar_diagnostics.py'),
        (Join-Path $projectRoot 'scripts\wallet_radar_live.py'),
        (Join-Path $projectRoot 'requirements-wallet-radar-live.txt')
    )
    foreach ($source in $sources) {
        if (-not (Test-Path -LiteralPath $source)) { throw ('Fichier manquant : ' + $source) }
    }
    if ($AllowRunning) { Write-Host 'Le diagnostic peut tourner pendant la collecte ; il ne redemarre pas l''essai.' }
    else { Write-Host 'Si la surveillance tourne, arrete-la avec Ctrl+C dans son autre fenetre.' }
    Write-Host 'Connexion au VPS. Le mot de passe habituel peut etre demande deux fois.'
    Write-Host 'Les cles API et les reglages GMGN sont conserves.'
    & scp.exe @sources bot@2.24.62.69:/home/bot/wallet-alerts/scripts/
    if ($LASTEXITCODE -ne 0) { throw 'Transfert interrompu. Aucune gestion lancee.' }
}

function Invoke-WalletManager {
    Write-Host 'WALLET RADAR - Gestion des wallets' -ForegroundColor Green
    Send-RadarScripts
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
        Write-Host '3 - Tester les conditions BBIG BOSS en SIMULATION (aucun achat reel)'
        Write-Host '4 - Afficher le bilan du dernier essai de 30 tokens'
        Write-Host '5 - Arreter proprement Wallet Radar sur le VPS'
        Write-Host '6 - Diagnostiquer les cas incomplets (lecture seule)'
        Write-Host '7 - Configurer le wallet de test REEL sur le VPS (saisie privee)'
        Write-Host '8 - Demarrer les essais REELS : 1 USD, budget 15 USD, SL/TP, sortie 60 s'
        Write-Host '9 - Afficher le bilan des transactions REELLES'
        Write-Host '10 - Verifier les acces REELS sans ordre ni ressaisie des cles'
        Write-Host '11 - Remplacer seulement la cle API Jupiter (wallet conserve)'
        Write-Host '12 - Diagnostiquer la sortie de la position ouverte (sans ordre)'
        Write-Host '13 - Rapprocher une vente manuelle verifiee et debloquer les achats (sans ordre)'
        Write-Host '0 - Quitter'
        $choice = Read-Host 'Ton choix'
        if ($choice -eq '0') { break }
        try {
            if ($choice -eq '1') {
                Send-RadarScripts
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'OUVRIR_CHARTS_GMGN.ps1') -SingleTab
            } elseif ($choice -eq '2') {
                Invoke-WalletManager
            } elseif ($choice -eq '3') {
                Send-RadarScripts
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'OUVRIR_CHARTS_GMGN.ps1') -SingleTab -PaperBuys
            } elseif ($choice -eq '4') {
                & ssh.exe -t bot@2.24.62.69 '/home/bot/wallet-alerts/.venv/bin/python /home/bot/wallet-alerts/scripts/wallet_alerts_ws.py --paper-report'
                if ($LASTEXITCODE -ne 0) { throw 'Bilan indisponible. Installer et lancer le mode 3 avant le premier bilan.' }
            } elseif ($choice -eq '5') {
                Send-RadarScripts
                & ssh.exe -t bot@2.24.62.69 '/home/bot/wallet-alerts/.venv/bin/python /home/bot/wallet-alerts/scripts/wallet_alerts_ws.py --stop'
                if ($LASTEXITCODE -ne 0) { throw 'Arret non confirme. Verifie les messages ci-dessus avant de relancer.' }
            } elseif ($choice -eq '6') {
                Send-RadarScripts -AllowRunning
                & ssh.exe -t bot@2.24.62.69 '/home/bot/wallet-alerts/.venv/bin/python /home/bot/wallet-alerts/scripts/wallet_alerts_ws.py --paper-diagnose'
                if ($LASTEXITCODE -ne 0) { throw 'Diagnostic indisponible. Verifie les messages ci-dessus.' }
            } elseif ($choice -eq '7') {
                Send-RadarScripts
                & ssh.exe -t bot@2.24.62.69 '/home/bot/wallet-alerts/.venv/bin/python -m pip install -r /home/bot/wallet-alerts/scripts/requirements-wallet-radar-live.txt && /home/bot/wallet-alerts/.venv/bin/python /home/bot/wallet-alerts/scripts/wallet_radar_live.py --setup-if-missing && /home/bot/wallet-alerts/.venv/bin/python /home/bot/wallet-alerts/scripts/wallet_radar_live.py --check'
                if ($LASTEXITCODE -ne 0) { throw 'Configuration reelle incomplete. Aucun ordre lance.' }
            } elseif ($choice -eq '8') {
                Send-RadarScripts
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'OUVRIR_CHARTS_GMGN.ps1') -SingleTab -LiveBuys
            } elseif ($choice -eq '9') {
                & ssh.exe -t bot@2.24.62.69 '/home/bot/wallet-alerts/.venv/bin/python /home/bot/wallet-alerts/scripts/wallet_radar_live.py --report'
                if ($LASTEXITCODE -ne 0) { throw 'Bilan reel indisponible.' }
            } elseif ($choice -eq '10') {
                Send-RadarScripts -AllowRunning
                & ssh.exe -t bot@2.24.62.69 '/home/bot/wallet-alerts/.venv/bin/python /home/bot/wallet-alerts/scripts/wallet_radar_live.py --check'
                if ($LASTEXITCODE -ne 0) { throw 'Controle incomplet : relever l etape et le code affiches, sans envoyer de cle.' }
            } elseif ($choice -eq '11') {
                Send-RadarScripts
                & ssh.exe -t bot@2.24.62.69 '/home/bot/wallet-alerts/.venv/bin/python /home/bot/wallet-alerts/scripts/wallet_radar_live.py --update-jupiter && /home/bot/wallet-alerts/.venv/bin/python /home/bot/wallet-alerts/scripts/wallet_radar_live.py --check'
                if ($LASTEXITCODE -ne 0) { throw 'Verification Jupiter incomplete ; cle wallet conservee.' }
            } elseif ($choice -eq '12') {
                Send-RadarScripts -AllowRunning
                & ssh.exe -t bot@2.24.62.69 '/home/bot/wallet-alerts/.venv/bin/python /home/bot/wallet-alerts/scripts/wallet_radar_live.py --diagnose-exit'
                if ($LASTEXITCODE -ne 0) { throw 'Diagnostic sortie interrompu ; aucun ordre demande.' }
            } elseif ($choice -eq '13') {
                Send-RadarScripts -AllowRunning
                & ssh.exe -t bot@2.24.62.69 '/home/bot/wallet-alerts/.venv/bin/python /home/bot/wallet-alerts/scripts/wallet_radar_live.py --reconcile-external-sale'
                if ($LASTEXITCODE -ne 0) { throw 'Rapprochement incomplet : journal conserve, aucun ordre envoye.' }
            } else { Write-Host 'Choisis un numero de 0 a 13.' }
        } catch { Write-Host $_.Exception.Message -ForegroundColor Red }
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
