# Chrome native-messaging host. STDOUT is exclusively the binary Chrome protocol.
$ErrorActionPreference = 'Stop'
$inputStream = [Console]::OpenStandardInput()
$outputStream = [Console]::OpenStandardOutput()
$utf8 = New-Object System.Text.UTF8Encoding($false)
$base = Join-Path $env:LOCALAPPDATA 'WalletAlertsGMGN'
$session = ''
$offset = [long]0

function Read-Exact {
    param([byte[]]$Buffer)
    $read = 0
    while ($read -lt $Buffer.Length) {
        $count = $inputStream.Read($Buffer, $read, $Buffer.Length - $read)
        if ($count -eq 0) { return $false }
        $read += $count
    }
    return $true
}

try {
    while ($true) {
        $header = New-Object byte[] 4
        if (-not (Read-Exact $header)) { break }
        $length = [BitConverter]::ToUInt32($header, 0)
        if ($length -lt 1 -or $length -gt 4096) { break }
        $body = New-Object byte[] $length
        if (-not (Read-Exact $body)) { break }
        $events = @(); $ok = $true
        try {
            $request = $utf8.GetString($body) | ConvertFrom-Json
            if ($request.action -cne 'poll') { throw 'Unknown request' }
            $sessionPath = Join-Path $base 'bridge-session.txt'
            if (Test-Path -LiteralPath $sessionPath) {
                $current = [IO.File]::ReadAllText($sessionPath).Trim()
                if ($current -cnotmatch '^[a-f0-9]{32}$') { throw 'Invalid session' }
                if ($current -cne $session) { $session = $current; $offset = 0 }
                $path = Join-Path $base ('gmgn-events-' + $session + '.jsonl')
                if (Test-Path -LiteralPath $path) {
                    $file = [IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
                    try {
                        if ($file.Length -lt $offset) { $offset = 0 }
                        [void]$file.Seek($offset,[IO.SeekOrigin]::Begin)
                        $buffer = New-Object byte[] 65536
                        $count = $file.Read($buffer,0,$buffer.Length)
                        # Consume complete lines only; a concurrent append may be incomplete.
                        $lastNewline = -1
                        for ($i=0; $i -lt $count; $i++) { if ($buffer[$i] -eq 10) { $lastNewline = $i } }
                        if ($lastNewline -ge 0) {
                            $text = $utf8.GetString($buffer,0,$lastNewline+1)
                            $offset += $lastNewline+1
                            foreach ($line in ($text -split "`n")) {
                                if ($line.Trim()) {
                                    try { $events += ($line | ConvertFrom-Json) } catch { }
                                }
                            }
                        }
                    } finally { $file.Dispose() }
                }
            }
        } catch { $ok = $false; $events = @() }
        $json = @{ok=$ok;events=@($events)} | ConvertTo-Json -Depth 8 -Compress
        $bytes = $utf8.GetBytes($json)
        $outputStream.Write([BitConverter]::GetBytes([uint32]$bytes.Length),0,4)
        $outputStream.Write($bytes,0,$bytes.Length)
        $outputStream.Flush()
    }
} finally {
    $inputStream.Dispose(); $outputStream.Dispose()
}
