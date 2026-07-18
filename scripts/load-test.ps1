param(
    [Parameter(Mandatory = $true)]
    [string]$BaseUrl,
    [Parameter(Mandatory = $true)]
    [string]$ApiKey,
    [ValidateRange(1, 100000)]
    [int]$Total = 100,
    [ValidateRange(1, 1000)]
    [int]$Concurrency = 20,
    [ValidateRange(1, 120)]
    [int]$TimeoutMinutes = 25,
    [string]$ConfigPath = "",
    [switch]$ReloadConfig
)

$ErrorActionPreference = "Stop"

# 生成单次 OpenAI Images 请求体。
function New-ImageRequestBody {
    param([int]$Index)

    return @{
        model = "gpt-image-2"
        prompt = "并发稳定性测试-$Index"
        response_format = "b64_json"
    } | ConvertTo-Json -Compress
}

# 创建只改变插件清理选项的配置副本，触发安全热重载。
function New-ReloadedConfig {
    param([string]$Config)

    if ($Config.Contains("cleanup_conversation: true")) {
        return $Config.Replace("cleanup_conversation: true", "cleanup_conversation: false")
    }
    if ($Config.Contains("cleanup_conversation: false")) {
        return $Config.Replace("cleanup_conversation: false", "cleanup_conversation: true")
    }
    throw "配置中缺少 cleanup_conversation，无法执行热重载测试。"
}

# 把异常归类为简短统计项，避免输出响应正文或敏感信息。
function Get-ErrorCategory {
    param([Exception]$Exception)

    if ($Exception -is [Threading.Tasks.TaskCanceledException]) {
        return "timeout"
    }
    return "transport"
}

Add-Type -AssemblyName System.Net.Http
$client = [Net.Http.HttpClient]::new()
$client.Timeout = [TimeSpan]::FromMinutes($TimeoutMinutes)
$client.DefaultRequestHeaders.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $ApiKey)
$endpoint = $BaseUrl.TrimEnd("/") + "/images/generations"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$originalConfig = $null
$reloadedConfig = $null
$statusCounts = @{}
$durations = [Collections.Generic.List[double]]::new()
$succeeded = 0
$failed = 0
$startedAt = Get-Date

if ($ReloadConfig) {
    if (-not $ConfigPath) {
        throw "启用 ReloadConfig 时必须提供 ConfigPath。"
    }
    $resolvedConfig = [IO.Path]::GetFullPath($ConfigPath)
    $originalConfig = [IO.File]::ReadAllText($resolvedConfig, [Text.Encoding]::UTF8)
    $reloadedConfig = New-ReloadedConfig -Config $originalConfig
}

try {
    for ($offset = 0; $offset -lt $Total; $offset += $Concurrency) {
        $batchSize = [Math]::Min($Concurrency, $Total - $offset)
        $requests = [Collections.Generic.List[object]]::new()

        # 每批同时发出 Concurrency 个请求，批次总和严格等于 Total。
        for ($position = 0; $position -lt $batchSize; $position++) {
            $index = $offset + $position + 1
            $body = New-ImageRequestBody -Index $index
            $content = [Net.Http.StringContent]::new($body, [Text.Encoding]::UTF8, "application/json")
            $watch = [Diagnostics.Stopwatch]::StartNew()
            $task = $client.PostAsync($endpoint, $content)
            $requests.Add([PSCustomObject]@{ Task = $task; Content = $content; Watch = $watch })
        }

        # 在请求执行期间交替变更配置，验证热重载不会打断在途请求。
        if ($ReloadConfig) {
            Start-Sleep -Milliseconds 150
            $configText = if ((($offset / $Concurrency) % 2) -eq 0) { $reloadedConfig } else { $originalConfig }
            [IO.File]::WriteAllText($resolvedConfig, $configText, $utf8NoBom)
        }

        foreach ($request in $requests) {
            try {
                $response = $request.Task.GetAwaiter().GetResult()
                $request.Watch.Stop()
                $durations.Add($request.Watch.Elapsed.TotalSeconds)
                $status = [int]$response.StatusCode
                $statusKey = [string]$status
                if (-not $statusCounts.ContainsKey($statusKey)) { $statusCounts[$statusKey] = 0 }
                $statusCounts[$statusKey]++
                $raw = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()

                if ($response.IsSuccessStatusCode) {
                    $parsed = $raw | ConvertFrom-Json
                    if ($parsed.data.Count -gt 0 -and ($parsed.data[0].b64_json -or $parsed.data[0].url)) {
                        $succeeded++
                    } else {
                        $failed++
                        if (-not $statusCounts.ContainsKey("invalid_response")) { $statusCounts["invalid_response"] = 0 }
                        $statusCounts["invalid_response"]++
                    }
                } else {
                    $failed++
                }
                $response.Dispose()
            } catch {
                $request.Watch.Stop()
                $durations.Add($request.Watch.Elapsed.TotalSeconds)
                $failed++
                $category = Get-ErrorCategory -Exception $_.Exception
                if (-not $statusCounts.ContainsKey($category)) { $statusCounts[$category] = 0 }
                $statusCounts[$category]++
            } finally {
                $request.Content.Dispose()
            }
        }

        $completed = [Math]::Min($offset + $batchSize, $Total)
        $elapsed = (Get-Date) - $startedAt
        Write-Output ("进度 {0}/{1}，成功 {2}，失败 {3}，已用时 {4}" -f $completed, $Total, $succeeded, $failed, $elapsed.ToString("hh\:mm\:ss"))
    }
} finally {
    if ($ReloadConfig -and $null -ne $originalConfig) {
        [IO.File]::WriteAllText($resolvedConfig, $originalConfig, $utf8NoBom)
    }
    $client.Dispose()
}

$totalElapsed = (Get-Date) - $startedAt
$average = if ($durations.Count -gt 0) { ($durations | Measure-Object -Average).Average } else { 0 }
$maximum = if ($durations.Count -gt 0) { ($durations | Measure-Object -Maximum).Maximum } else { 0 }
$statusSummary = ($statusCounts.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ", "
Write-Output ("汇总：总数 {0}，并发 {1}，成功 {2}，失败 {3}，平均 {4:N2}s，最大 {5:N2}s，总耗时 {6}" -f $Total, $Concurrency, $succeeded, $failed, $average, $maximum, $totalElapsed.ToString("hh\:mm\:ss"))
Write-Output ("状态：{0}" -f $statusSummary)

if ($failed -gt 0 -or $succeeded -ne $Total) {
    exit 1
}
