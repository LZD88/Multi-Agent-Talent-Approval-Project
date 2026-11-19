param(
    [switch]$CheckOnly,
    [string]$VmAddress = '192.168.163.128',
    [string]$Vmx = 'D:\WorkSpace\CentOS 7.vmx'
)

$ErrorActionPreference = 'Stop'
$checks = [ordered]@{
    Frontend = 'http://127.0.0.1:15173/'
    Backend = 'http://127.0.0.1:18080/api/health'
    MinerU = 'http://127.0.0.1:18001/health'
    Milvus = 'http://127.0.0.1:19091/healthz'
}
$ready = $true
foreach ($item in $checks.GetEnumerator()) {
    try {
        $response = Invoke-WebRequest -Uri $item.Value -TimeoutSec 4 -UseBasicParsing
        $healthy = $response.StatusCode -eq 200
        if ($item.Key -eq 'MinerU') {
            $healthy = $healthy -and (($response.Content | ConvertFrom-Json).status -eq 'healthy')
        }
    } catch { $healthy = $false }
    Write-Host "$($item.Key): $healthy"
    $ready = $ready -and $healthy
}
if ($CheckOnly) {
    if ($ready) { exit 0 } else { exit 1 }
}
if ($ready) {
    Write-Host 'Project ready: http://127.0.0.1:15173/'
    exit 0
}

function Test-VmSsh {
    $connection = [System.Net.Sockets.TcpClient]::new()
    try {
        $attempt = $connection.ConnectAsync($VmAddress, 22)
        return $attempt.Wait(2000) -and $connection.Connected
    } catch { return $false } finally { $connection.Dispose() }
}

if (-not (Test-VmSsh)) {
    $vmware = 'D:\VMware\vmware.exe'
    if (-not (Test-Path -LiteralPath $Vmx)) { throw "VM file missing: $Vmx" }
    Start-Process -FilePath $vmware -ArgumentList '-x', ('"' + $Vmx + '"') -WindowStyle Hidden
    $deadline = (Get-Date).AddMinutes(3)
    while (-not (Test-VmSsh)) {
        if ((Get-Date) -gt $deadline) { throw "VM SSH unavailable at ${VmAddress}:22; check ens33 IPv4 in VMware." }
        Start-Sleep -Seconds 3
    }
}

$sshArguments = @('-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30')
foreach ($port in @(15173, 18080, 18001, 19000, 19001, 15432, 16379, 19531, 19091)) {
    $sshArguments += @('-L', "127.0.0.1:${port}:127.0.0.1:${port}")
}
$names = @('postgres', 'minio', 'redis', 'etcd', 'milvus', 'mineru', 'backend', 'worker', 'frontend') |
    ForEach-Object { "talent-eval-agents-course_${_}_1" }
$remoteCommand = 'systemctl start docker && docker start ' + ($names -join ' ') +
    ' && echo "Open http://127.0.0.1:15173/ - keep this terminal open" && while true; do sleep 60; done'
$sshArguments += @("root@$VmAddress", $remoteCommand)
Write-Host 'Enter the existing CentOS root password when SSH asks. Keep this terminal open for port forwarding.'
& ssh.exe @sshArguments
exit $LASTEXITCODE
