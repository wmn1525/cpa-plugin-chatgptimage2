param(
    [Parameter(Mandatory = $true)]
    [string]$CpaDir
)

$ErrorActionPreference = "Stop"
$cpaRoot = [IO.Path]::GetFullPath($CpaDir)
$target = Join-Path $cpaRoot "plugins\windows\amd64"

# 同时支持发布包根目录和源码仓库 scripts 目录。
$sourceRoot = $PSScriptRoot
if (-not (Test-Path (Join-Path $sourceRoot "cpaimage.dll"))) {
    $parentRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
    if (Test-Path (Join-Path $parentRoot "cpaimage.dll")) {
        $sourceRoot = $parentRoot
    } else {
        $sourceRoot = Join-Path $parentRoot "dist"
    }
}
$dll = Join-Path $sourceRoot "cpaimage.dll"
if (-not (Test-Path $dll)) {
    throw "当前目录未找到 cpaimage.dll。"
}

# 清理商店或旧安装留下的版本动态库，避免 CPA 优先加载旧版本。
New-Item -ItemType Directory -Force -Path $target | Out-Null
Get-ChildItem -LiteralPath $target -Filter "cpaimage-v*.dll" -File -ErrorAction SilentlyContinue |
    Remove-Item -Force
Remove-Item -LiteralPath (Join-Path $target "cpaimage-helper.exe") -Force -ErrorAction SilentlyContinue
Copy-Item -LiteralPath $dll -Destination $target -Force

$snippet = @"
plugins:
  enabled: true
  dir: "plugins"
  configs:
    cpaimage:
      enabled: true
      priority: 100
      base_url: "https://chatgpt.com"
      request_timeout: "20m"
      proxy_url: ""
      cf_cookies: ""
      cleanup_conversation: true
"@
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $target "cpaimage-config.yaml"), $snippet, $utf8NoBom)

Write-Output "插件文件已安装到：$target"
Write-Output "请把以下内容合并到 CPA 的 config.yaml，然后完全重启 CPA："
Write-Output $snippet
