param(
    [string]$CpaExe = "$PSScriptRoot\..\build\integration\cli-proxy-api.exe"
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$runtime = Join-Path $root "build\integration\runtime"
$plugins = Join-Path $runtime "plugins"
$auth = Join-Path $runtime "auth"

# 重新创建隔离的 CPA 集成测试目录。
if (Test-Path $runtime) {
    Remove-Item -LiteralPath $runtime -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $plugins, $auth | Out-Null
Copy-Item -LiteralPath (Join-Path $root "dist\cpaimage.dll") -Destination $plugins
Copy-Item -LiteralPath (Join-Path $root "dist\cpaimage-helper.exe") -Destination $plugins

# 写入不包含真实凭证的测试账号和 CPA 配置。
$authJson = @'
{"type":"codex","email":"mock@example.com","access_token":"eyJhbGciOiJub25lIn0.eyJleHAiOjQxMDI0NDQ4MDAsImh0dHBzOi8vYXBpLm9wZW5haS5jb20vYXV0aCI6eyJjaGF0Z3B0X2FjY291bnRfaWQiOiJhY2NfbW9jayIsImNoYXRncHRfcGxhbl90eXBlIjoicGx1cyJ9fQ.x"}
'@
$authJson2 = @'
{"type":"codex","email":"mock2@example.com","access_token":"eyJhbGciOiJub25lIn0.eyJleHAiOjQxNDI0NDQ4MDAsImh0dHBzOi8vYXBpLm9wZW5haS5jb20vYXV0aCI6eyJjaGF0Z3B0X2FjY291bnRfaWQiOiJhY2NfbW9jazIiLCJjaGF0Z3B0X3BsYW5fdHlwZSI6InBsdXMifX0.y"}
'@
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $auth "mock.json"), $authJson.Trim(), $utf8NoBom)

$authPath = $auth.Replace("\", "/")
$pluginPath = $plugins.Replace("\", "/")
$helperPath = (Join-Path $plugins "cpaimage-helper.exe").Replace("\", "/")
$configYaml = @"
host: "127.0.0.1"
port: 18317
auth-dir: "$authPath"
api-keys:
  - "integration-key"
debug: true
plugins:
  enabled: true
  dir: "$pluginPath"
  configs:
    cpaimage:
      enabled: true
      priority: 100
      base_url: "http://127.0.0.1:18081"
      request_timeout: "30s"
      cleanup_conversation: true
      helper_path: "$helperPath"
"@
[IO.File]::WriteAllText((Join-Path $runtime "config.yaml"), $configYaml, $utf8NoBom)

$mock = $null
$cpa = $null
try {
    # 启动模拟 ChatGPT 与真实 CPA 进程。
    $mock = Start-Process -FilePath "py" -ArgumentList "-3.12", "-m", "tests.mock_server_entry", "--port", "18081", "--generation-delay", "0.35" -WorkingDirectory $root -PassThru -WindowStyle Hidden
    $cpa = Start-Process -FilePath $CpaExe -ArgumentList "--config", (Join-Path $runtime "config.yaml") -WorkingDirectory $runtime -RedirectStandardOutput (Join-Path $runtime "cpa.stdout.log") -RedirectStandardError (Join-Path $runtime "cpa.stderr.log") -PassThru -WindowStyle Hidden

    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:18317/v1/models" -Headers @{Authorization="Bearer integration-key"} | Out-Null
            $ready = $true
            break
        } catch {}
    }
    if (-not $ready) {
        throw "CPA 未在预期时间内启动。"
    }

    # 通过 CPA 的真实 Images API 验证插件路由和最终图片响应。
    $body = @{model="gpt-image-2"; prompt="一只猫"; response_format="b64_json"} | ConvertTo-Json -Compress
    $response = Invoke-RestMethod "http://127.0.0.1:18317/v1/images/generations" -Method Post -ContentType "application/json" -Headers @{Authorization="Bearer integration-key"} -Body $body
    if (-not $response.data[0].b64_json) {
        throw "CPA 插件未返回 b64_json 图片。"
    }

    # 热上传第二个凭证，后续请求应直接从 CPA 发现而无需重启插件。
    [IO.File]::WriteAllText((Join-Path $auth "mock2.json"), $authJson2.Trim(), $utf8NoBom)
    Start-Sleep -Seconds 2

    # 并发请求期间重复写入和变更配置，所有在途生成必须完成。
    Add-Type -AssemblyName System.Net.Http
    $client = [Net.Http.HttpClient]::new()
    $client.DefaultRequestHeaders.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", "integration-key")
    $tasks = [Collections.Generic.List[Threading.Tasks.Task]]::new()
    for ($index = 0; $index -lt 8; $index++) {
        $concurrentBody = @{model="gpt-image-2"; prompt="并发猫-$index"; response_format="b64_json"} | ConvertTo-Json -Compress
        $content = [Net.Http.StringContent]::new($concurrentBody, [Text.Encoding]::UTF8, "application/json")
        $tasks.Add($client.PostAsync("http://127.0.0.1:18317/v1/images/generations", $content))
    }
    Start-Sleep -Milliseconds 300
    [IO.File]::WriteAllText((Join-Path $runtime "config.yaml"), $configYaml, $utf8NoBom)
    Start-Sleep -Milliseconds 300
    $changedConfig = $configYaml.Replace("cleanup_conversation: true", "cleanup_conversation: false")
    [IO.File]::WriteAllText((Join-Path $runtime "config.yaml"), $changedConfig, $utf8NoBom)
    if (-not [Threading.Tasks.Task]::WaitAll($tasks.ToArray(), 30000)) {
        throw "配置热切换期间并发请求未在超时内完成。"
    }
    foreach ($task in $tasks) {
        $httpResponse = [Net.Http.HttpResponseMessage]$task.Result
        $rawBody = $httpResponse.Content.ReadAsStringAsync().Result
        if (-not $httpResponse.IsSuccessStatusCode) {
            throw "配置热切换期间请求失败：HTTP $([int]$httpResponse.StatusCode) $rawBody"
        }
        $parsedBody = $rawBody | ConvertFrom-Json
        if (-not $parsedBody.data[0].b64_json) {
            throw "配置热切换期间请求未返回图片。"
        }
    }
    $client.Dispose()
    $authCount = (Invoke-RestMethod "http://127.0.0.1:18081/__test__/auth-count").count
    if ($authCount -lt 2) {
        throw "并发请求未使用热上传的第二个 CPA 凭证。"
    }

    # 验证多图与流式请求均经过插件执行器。
    $multiBody = @{model="gpt-image-2"; prompt="两只猫"; n=2; response_format="b64_json"} | ConvertTo-Json -Compress
    $multi = Invoke-RestMethod "http://127.0.0.1:18317/v1/images/generations" -Method Post -ContentType "application/json" -Headers @{Authorization="Bearer integration-key"} -Body $multiBody
    if ($multi.data.Count -ne 2) {
        throw "CPA 插件多图结果数量不正确。"
    }
    $streamBody = @{model="gpt-image-2"; prompt="流式猫"; stream=$true} | ConvertTo-Json -Compress
    $streamBodyPath = Join-Path $runtime "stream.json"
    [IO.File]::WriteAllText($streamBodyPath, $streamBody, $utf8NoBom)
    $streamResult = & curl.exe -sS "http://127.0.0.1:18317/v1/images/generations" -X POST -H "Authorization: Bearer integration-key" -H "Content-Type: application/json" --data-binary "@$streamBodyPath"
    if (($streamResult -join "`n") -notmatch "image.generation.result") {
        throw "CPA 插件流式结果格式不正确：$($streamResult -join ' ')"
    }

    # 验证真实 multipart 图片编辑入口。
    $pngPath = Join-Path $runtime "input.png"
    [IO.File]::WriteAllBytes($pngPath, [Convert]::FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
    $editRaw = & curl.exe -sS "http://127.0.0.1:18317/v1/images/edits" -X POST -H "Authorization: Bearer integration-key" -F "model=gpt-image-2" -F "prompt=修改图片" -F "image=@$pngPath;type=image/png"
    $edit = ($editRaw -join "`n") | ConvertFrom-Json
    if (-not $edit.data[0].b64_json) {
        throw "CPA 插件 multipart 编辑未返回图片。"
    }
    Write-Output "CPA 集成测试通过：插件已加载并完成网页生图模拟链路。"
} finally {
    if ($cpa -and -not $cpa.HasExited) { Stop-Process -Id $cpa.Id -Force }
    if ($mock -and -not $mock.HasExited) { Stop-Process -Id $mock.Id -Force }
}
