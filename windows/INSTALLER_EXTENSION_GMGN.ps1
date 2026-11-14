$ErrorActionPreference = 'Stop'
try {
    $id = 'hiiecbdabaichplolfmoelhjlojbffje'
    $destination = Join-Path $env:LOCALAPPDATA 'WalletAlertsGMGN\nativehost'
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    foreach ($name in @('GMGN_NATIVE_HOST.cmd','GMGN_NATIVE_HOST.ps1')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $destination $name) -Force
    }
    $manifest = @{
        name='fr.walletalerts.gmgn'; description='Public verified buy events for GMGN charts';
        path=(Join-Path $destination 'GMGN_NATIVE_HOST.cmd'); type='stdio';
        allowed_origins=@('chrome-extension://' + $id + '/')
    }
    $manifestPath = Join-Path $destination 'fr.walletalerts.gmgn.json'
    [IO.File]::WriteAllText($manifestPath,($manifest | ConvertTo-Json -Depth 4),(New-Object System.Text.UTF8Encoding($false)))
    $registry = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\fr.walletalerts.gmgn'
    New-Item -Path $registry -Force | Out-Null
    Set-Item -Path $registry -Value $manifestPath
    Write-Host 'Pont local installe pour ton compte Windows. Aucun droit administrateur necessaire.' -ForegroundColor Green
    Write-Host 'Dans Chrome : chrome://extensions -> Mode developpeur -> Charger l''extension non empaquetee.'
    Write-Host ('Selectionne : ' + (Join-Path (Split-Path $PSScriptRoot -Parent) 'chrome-gmgn'))
    Write-Host ('Identifiant attendu : ' + $id)
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
