# Backup / restore of Network Commander data. Credentials are DPAPI-bound to THIS Windows user:
# a restored copy only decrypts for the same user on the same machine profile (re-enter credentials otherwise).
param([ValidateSet("backup","restore")]$Action="backup", $File="$env:USERPROFILE\NetworkCommander-data.zip")
$dir = "$env:LOCALAPPDATA\NetworkCommander"
& "$env:ProgramFiles\NetworkCommander\NetworkCommander.exe" --stop 2>$null
if ($Action -eq "backup") { Compress-Archive -Path "$dir\*" -DestinationPath $File -Force; "Saved $File" }
else { Expand-Archive -Path $File -DestinationPath $dir -Force; "Restored from $File" }
