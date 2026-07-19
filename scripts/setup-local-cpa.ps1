param(
    [string]$InstallDir = "$PSScriptRoot\..\local-cpa",
    [string]$Version = "7.2.86"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not (Test-Path (Join-Path $root "cpaimage.dll"))) {
    $root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
}
$install = [IO.Path]::GetFullPath($InstallDir)
$archive = Join-Path $env:TEMP "CLIProxyAPI_${Version}_windows_amd64.zip"
$url = "https://github.com/router-for-me/CLIProxyAPI/releases/download/v$Version/CLIProxyAPI_${Version}_windows_amd64.zip"

# 下载并解压支持动态库插件的 Windows CPA。
New-Item -ItemType Directory -Force -Path $install | Out-Null
if (-not (Test-Path (Join-Path $install "cli-proxy-api.exe"))) {
    Invoke-WebRequest -UseBasicParsing $url -OutFile $archive
    Expand-Archive -Path $archive -DestinationPath $install -Force
}

# 安装本项目已经构建好的插件产物。
$installScript = Join-Path $root "install.ps1"
if (-not (Test-Path $installScript)) {
    $installScript = Join-Path $root "scripts\install.ps1"
}
& $installScript -CpaDir $install
$authPath = (Join-Path $install "auth").Replace("\", "/")
$pluginPath = (Join-Path $install "plugins").Replace("\", "/")
New-Item -ItemType Directory -Force -Path (Join-Path $install "auth") | Out-Null

# 为新的本地目录生成可直接启动的最小配置。
$config = @"
host: "127.0.0.1"
port: 8317
auth-dir: "$authPath"
api-keys:
  - "local-cpa-key"
remote-management:
  allow-remote: false
  secret-key: "local-management-key"
debug: false
plugins:
  enabled: true
  dir: "$pluginPath"
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
[IO.File]::WriteAllText((Join-Path $install "config.yaml"), $config, $utf8NoBom)

# 在独立窗口中启动本地 CPA，便于查看日志和停止服务。
Start-Process -FilePath (Join-Path $install "cli-proxy-api.exe") -ArgumentList "--config", (Join-Path $install "config.yaml") -WorkingDirectory $install
Write-Output "本地 CPA 已启动：http://127.0.0.1:8317"
Write-Output "管理页面：http://127.0.0.1:8317/management.html"
Write-Output "管理密钥：local-management-key"
Write-Output "API Key：local-cpa-key"
