param([string]$SourcePython, [string]$Root = 'C:\Users\Unik\.local\share\knowi-backups')
$ErrorActionPreference = 'Stop'
$taskSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
New-Item -ItemType Directory -Path $Root -Force | Out-Null
& icacls $Root /inheritance:r /grant:r "*${taskSid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Private backup directory ACL failed' }
foreach ($name in @('keys','archives','state','verify','bin')) { New-Item -ItemType Directory -Path (Join-Path $Root $name) -Force | Out-Null }
$openssl = 'C:\Program Files\Git\usr\bin\openssl.exe'
$private = Join-Path $Root 'keys\decrypt.pem'
$public = Join-Path $Root 'keys\recipient.pem'
if ((Test-Path -LiteralPath $private) -ne (Test-Path -LiteralPath $public)) { throw 'Partial encryption key setup; reconcile before continuing' }
if (-not (Test-Path -LiteralPath $private)) {
    & $openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out $private 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Backup private key generation failed' }
    & $openssl req -new -x509 -key $private -out $public -days 3650 -subj '/CN=knowi-backup-recipient-20261113' 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Backup public certificate generation failed' }
}
$sshKey = Join-Path $Root 'keys\pull_ed25519'
if (-not (Test-Path -LiteralPath $sshKey)) {
    & 'C:\Windows\System32\OpenSSH\ssh-keygen.exe' -q -t ed25519 -N '' -C 'knowi-backup-pull-only' -f $sshKey
    if ($LASTEXITCODE -ne 0) { throw 'Restricted pull SSH key generation failed' }
}
$python = Join-Path $Root 'runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & $SourcePython -m venv (Join-Path $Root 'runtime')
    if ($LASTEXITCODE -ne 0) { throw 'Backup client runtime creation failed' }
}
& $python -m pip install --disable-pip-version-check --only-binary=:all: 'paramiko==4.0.0'
if ($LASTEXITCODE -ne 0) { throw 'Backup transport installation failed' }
$taskCode = @'
import json,sys
from pathlib import Path
import paramiko
root=Path(sys.argv[1])
hosts=paramiko.HostKeys(str(Path.home()/'.ssh/known_hosts'))
entry=hosts.lookup('13.140.29.11')
assert entry, 'Verified server host key is absent'
out=''.join('13.140.29.11 '+kind+' '+key.get_base64()+'\n' for kind,key in entry.items())
if not (root/'keys/known_hosts').exists(): (root/'keys/known_hosts').write_text(out)
metadata={'recipient_pem':(root/'keys/recipient.pem').read_text(),'ssh_public':(root/'keys/pull_ed25519.pub').read_text().strip()}
Path('output/knowi-backup-public.json').write_text(json.dumps(metadata))
(root/'client.json').write_text(json.dumps({'host':'13.140.29.11','port':22,'user':'root','openssl':r'C:\Program Files\Git\usr\bin\openssl.exe'}))
print('Private backup client material prepared; no private key uploaded.')
'@
$taskCode | & $python - $Root
if ($LASTEXITCODE -ne 0) { throw 'Backup client host-key pinning failed' }
