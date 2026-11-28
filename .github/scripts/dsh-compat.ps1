param(
    [Parameter(Mandatory)][string]$Root,
    [string]$Version,
    [switch]$PrepareGateway,
    [switch]$InstallHarness
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

if ($PrepareGateway) {
    New-Item -ItemType Directory $Root -Force | Out-Null
    python -m venv "$Root/gateway"
    & "$Root/gateway/Scripts/python.exe" -m pip install --no-deps --only-binary=:all: -r requirements/gateway.txt
    & "$Root/gateway/Scripts/python.exe" -m pip install --no-deps .
    New-Item -ItemType File "$Root/multisim.local.toml" | Out-Null
    exit 0
}

if ($Version -cnotmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw 'Expected a single exact Harness version.'
}
$runtime = Join-Path $Root $Version
if ($InstallHarness) {
    npm install --prefix $runtime --ignore-scripts --no-audit --no-fund `
        "@deepseek-ai/dsh@$Version" 'pnpm@11.28.5'
    exit 0
}

$archive = npm pack ./integrations/deepseek-harness --pack-destination $Root
python tests/smoke_dsh.py --cli "$runtime/node_modules/@deepseek-ai/dsh/lib/bin.js" `
    --pnpm-bin "$runtime/node_modules/.bin" --archive (Join-Path $Root $archive) --install-dir $Root
