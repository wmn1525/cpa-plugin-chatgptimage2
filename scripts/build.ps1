param(
    [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$dist = Join-Path $root "dist"
$packageName = "cpaimage_${Version}_windows_amd64"
$packageDir = Join-Path $dist $packageName

# 检查 Windows CGO 和 Python 打包工具。
if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
    throw "未找到 Go 1.26，请先安装 Go。"
}
if (-not (Get-Command gcc -ErrorAction SilentlyContinue)) {
    throw "未找到 MinGW-w64 GCC，请先安装 WinLibs。"
}
py -3.12 -c "import curl_cffi, PIL, PyInstaller" | Out-Null

# 运行全部源码测试并构建 DLL。
Push-Location $root
try {
    Get-ChildItem -Filter *.go | ForEach-Object { gofmt -w $_.FullName }
    go test -race .
    go vet .
    py -3.12 -m unittest -v tests.test_helper

    New-Item -ItemType Directory -Force -Path $dist | Out-Null
    $env:CGO_ENABLED = "1"
    go build -trimpath -buildmode=c-shared -ldflags "-s -w -X main.pluginVersion=$Version" -o (Join-Path $dist "cpaimage.dll") .
    $header = Join-Path $dist "cpaimage.h"
    if (Test-Path $header) { Remove-Item -LiteralPath $header -Force }

    # 构建包含 curl_cffi 和 Pillow 的单文件助手。
    py -3.12 -m PyInstaller --noconfirm --clean --onefile --name cpaimage-helper `
        --distpath $dist --workpath (Join-Path $root "build\pyinstaller") `
        --specpath (Join-Path $root "build") (Join-Path $root "helper_entry.py")
    py -3.12 -m unittest -v tests.test_helper_exe

    # 组装可直接安装的发布目录和校验文件。
    if (Test-Path $packageDir) { Remove-Item -LiteralPath $packageDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $packageDir | Out-Null
    Copy-Item (Join-Path $dist "cpaimage.dll"), (Join-Path $dist "cpaimage-helper.exe"), `
        (Join-Path $root "README.md"), (Join-Path $root "README_CN.md"), `
        (Join-Path $root "config.example.yaml"), `
        (Join-Path $root "THIRD_PARTY_NOTICES.md"), (Join-Path $root "scripts\install.ps1"), `
        (Join-Path $root "scripts\setup-local-cpa.ps1") `
        -Destination $packageDir
    $hashLines = Get-ChildItem $packageDir -File | ForEach-Object {
        $hash = Get-FileHash -Algorithm SHA256 $_.FullName
        "$($hash.Hash.ToLower())  $($_.Name)"
    }
    [IO.File]::WriteAllLines((Join-Path $packageDir "checksums.txt"), $hashLines)
    $zipPath = Join-Path $dist ($packageName + ".zip")
    Compress-Archive -Path (Join-Path $packageDir "*") -DestinationPath $zipPath -Force
    Write-Output "构建完成：$zipPath"
} finally {
    Pop-Location
}
