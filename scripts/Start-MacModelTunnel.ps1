param(
    [string]$MacHost = 'Godwins-MacBook-Pro.local',
    [string]$MacUser = 'godwinlorenzllabres',
    [string]$IdentityFile = (Join-Path $env:USERPROFILE '.ssh\sevi_nova_mac_ed25519')
)

Write-Host 'Connecting Sevi and Nova to the Mac model on http://127.0.0.1:11435/v1'
Write-Host 'Using the dedicated SSH key. Keep this window open.'
& ssh -N -i $IdentityFile -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:11435:127.0.0.1:11434 "$MacUser@$MacHost"
if ($LASTEXITCODE -ne 0) {
    Write-Host "SSH exited with code $LASTEXITCODE. Check the Mac login and network connection."
    Read-Host 'Press Enter to close'
}
