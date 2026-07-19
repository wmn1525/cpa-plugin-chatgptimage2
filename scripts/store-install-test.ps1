param(
    [string]$CpaExe = "$PSScriptRoot\..\build\integration\cli-proxy-api.exe",
    [string]$Version = "0.1.8"
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$runtime = Join-Path $root "build\store-install\runtime"
$web = Join-Path $runtime "web"
$plugins = Join-Path $runtime "plugins"
$zipName = "cpaimage_${Version}_windows_amd64.zip"
$zipSource = Join-Path $root "dist\$zipName"
$registryPort = 18082
$cpaPort = 18318
$managementKey = "store-test-management-key"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

if (-not (Test-Path $zipSource)) {
    throw "未找到 Windows 商店 ZIP：$zipSource"
}
if (Test-Path $runtime) {
    Remove-Item -LiteralPath $runtime -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $web, $plugins | Out-Null
Copy-Item -LiteralPath $zipSource -Destination $web

# 生成指向本地发布 ZIP 的直接安装 registry。
$zipHash = (Get-FileHash -LiteralPath $zipSource -Algorithm SHA256).Hash.ToLower()
$registry = @{
    schema_version = 2
    plugins = @(@{
        id = "cpaimage"
        name = "CPA ChatGPT Web Image"
        description = "Intercepts gpt-image-2 Images API requests through the ChatGPT web picture_v2 flow."
        author = "wmn1525"
        version = $Version
        repository = "https://github.com/wmn1525/cpa-plugin-chatgptimage2"
        homepage = "https://github.com/wmn1525/cpa-plugin-chatgptimage2"
        license = "MIT"
        tags = @("Executor", "Model Router", "Images", "Codex")
        install = @{
            type = "direct"
            artifacts = @(@{
                goos = "windows"
                goarch = "amd64"
                url = "http://127.0.0.1:$registryPort/$zipName"
                sha256 = $zipHash
            })
        }
    })
}
[IO.File]::WriteAllText((Join-Path $web "registry.json"),
    ($registry | ConvertTo-Json -Depth 10), $utf8NoBom)

$pluginPath = $plugins.Replace("\", "/")
$configPath = Join-Path $runtime "config.yaml"
$config = @"
host: "127.0.0.1"
port: $cpaPort
api-keys:
  - "store-test-key"
remote-management:
  allow-remote: false
  secret-key: "$managementKey"
plugins:
  enabled: true
  dir: "$pluginPath"
  store-sources:
    - "http://127.0.0.1:$registryPort/registry.json"
  store-auth:
    - match: "http://127.0.0.1:$registryPort/"
      apply-to: ["registry", "artifact"]
      type: none
      allow-insecure: true
  configs: {}
"@
[IO.File]::WriteAllText($configPath, $config, $utf8NoBom)

# 等待 CPA 管理接口可用。
function Wait-CpaReady {
    param([Diagnostics.Process]$Process)
    for ($index = 0; $index -lt 60; $index++) {
        if ($Process.HasExited) { throw "CPA 提前退出。" }
        try {
            Invoke-RestMethod "http://127.0.0.1:$cpaPort/v0/management/plugin-store" `
                -Headers @{"X-Management-Key" = $managementKey} | Out-Null
            return
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "CPA 管理接口未在预期时间内启动。"
}

$server = $null
$cpa = $null
try {
    $server = Start-Process -FilePath "py" -ArgumentList "-3.12", "-m", "http.server", `
        "$registryPort", "--bind", "127.0.0.1", "--directory", $web `
        -WorkingDirectory $runtime -PassThru -WindowStyle Hidden
    $stdout = Join-Path $runtime "cpa.stdout.log"
    $stderr = Join-Path $runtime "cpa.stderr.log"
    $cpa = Start-Process -FilePath $CpaExe -ArgumentList "--config", $configPath `
        -WorkingDirectory $runtime -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr -PassThru -WindowStyle Hidden
    Wait-CpaReady -Process $cpa

    # 通过真实管理 API 完成一键安装并验证文件名。
    $installed = Invoke-RestMethod "http://127.0.0.1:$cpaPort/v0/management/plugin-store/cpaimage/install" `
        -Method Post -ContentType "application/json" -Headers @{"X-Management-Key" = $managementKey} `
        -Body (@{version = $Version} | ConvertTo-Json -Compress)
    if ($installed.status -ne "installed" -or $installed.version -ne $Version) {
        throw "商店安装响应异常：$($installed | ConvertTo-Json -Compress)"
    }
    $expectedFile = Join-Path $plugins "windows\amd64\cpaimage-v$Version.dll"
    if (-not (Test-Path $expectedFile)) {
        throw "商店未生成预期版本文件：$expectedFile"
    }

    # 重启后验证 CPA 仍加载 0.1.8，且商店元数据仓库地址正确。
    Stop-Process -Id $cpa.Id -Force
    $cpa.WaitForExit()
    $cpa = Start-Process -FilePath $CpaExe -ArgumentList "--config", $configPath `
        -WorkingDirectory $runtime -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr -PassThru -WindowStyle Hidden
    Wait-CpaReady -Process $cpa
    $store = Invoke-RestMethod "http://127.0.0.1:$cpaPort/v0/management/plugin-store" `
        -Headers @{"X-Management-Key" = $managementKey}
    $entry = $store.plugins | Where-Object {$_.id -eq "cpaimage"} | Select-Object -First 1
    if (-not $entry -or -not $entry.registered -or $entry.installed_version -ne $Version -or
        $entry.repository -ne "https://github.com/wmn1525/cpa-plugin-chatgptimage2") {
        throw "重启后的商店元数据异常。"
    }
    Stop-Process -Id $cpa.Id -Force
    $cpa.WaitForExit()
    $logText = [IO.File]::ReadAllText($stdout)
    if ($logText -notmatch "plugin_id=cpaimage.*version=$([Regex]::Escape($Version))") {
        throw "CPA 重启后未记录 cpaimage $Version 注册信息。"
    }
    Write-Output "CPA 商店一键安装测试通过：文件、版本、仓库地址和重启加载均正确。"
} finally {
    if ($cpa -and -not $cpa.HasExited) { Stop-Process -Id $cpa.Id -Force }
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
}
