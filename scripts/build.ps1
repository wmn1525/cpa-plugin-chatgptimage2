param(
    [string]$Version = "0.1.8"
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$dist = Join-Path $root "dist"
$payloadDir = Join-Path $root "helper_payload"
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

# 运行全部源码测试并构建自包含 DLL。
Push-Location $root
try {
    Get-ChildItem -Filter *.go | ForEach-Object { gofmt -w $_.FullName }
    go test -race .
    if ($LASTEXITCODE -ne 0) { throw "Go race 测试失败。" }
    go vet .
    if ($LASTEXITCODE -ne 0) { throw "Go vet 失败。" }
    py -3.12 -m unittest -v tests.test_helper
    if ($LASTEXITCODE -ne 0) { throw "Python 助手测试失败。" }

    # 构建包含 curl_cffi 和 Pillow 的单文件助手。
    New-Item -ItemType Directory -Force -Path $dist | Out-Null
    py -3.12 -m PyInstaller --noconfirm --clean --onefile --name cpaimage-helper `
        --distpath $dist --workpath (Join-Path $root "build\pyinstaller") `
        --specpath (Join-Path $root "build") (Join-Path $root "helper_entry.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 助手构建失败。" }
    py -3.12 -m unittest -v tests.test_helper_exe
    if ($LASTEXITCODE -ne 0) { throw "助手 EXE 测试失败。" }

    # 把平台助手嵌入 DLL，商店安装无需保留外部 sidecar。
    New-Item -ItemType Directory -Force -Path $payloadDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $dist "cpaimage-helper.exe") `
        -Destination (Join-Path $payloadDir "cpaimage-helper.bin") -Force
    $env:CGO_ENABLED = "1"
    go build -tags embedded_helper -trimpath -buildmode=c-shared `
        -ldflags "-s -w -X main.pluginVersion=$Version" -o (Join-Path $dist "cpaimage.dll") .
    if ($LASTEXITCODE -ne 0) { throw "Windows DLL 构建失败。" }
    $header = Join-Path $dist "cpaimage.h"
    if (Test-Path $header) { Remove-Item -LiteralPath $header -Force }
    $env:CPAIMAGE_EXPECT_VERSION = $Version
    go test -run '^TestRegistration$' -ldflags "-X=cpaimage.pluginVersion=$Version" .
    if ($LASTEXITCODE -ne 0) { throw "发布版本元数据校验失败。" }
    Remove-Item Env:CPAIMAGE_EXPECT_VERSION

    # 商店 ZIP 根目录只包含动态库。
    if (Test-Path $packageDir) { Remove-Item -LiteralPath $packageDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $packageDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $dist "cpaimage.dll") -Destination $packageDir
    $zipPath = Join-Path $dist ($packageName + ".zip")
    Compress-Archive -Path (Join-Path $packageDir "*") -DestinationPath $zipPath -Force
    Write-Output "构建完成：$zipPath"
} finally {
    Pop-Location
}
