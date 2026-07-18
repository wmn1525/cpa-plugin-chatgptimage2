param(
    [string]$Version = "0.1.0",
    [ValidateSet("amd64", "arm64", "all")]
    [string]$Arch = "all"
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$dist = Join-Path $root "dist"
$releaseBuild = Join-Path (Join-Path $root "build") "linux-release"
$normalizedVersion = $Version.Trim().TrimStart("v")

# 检查版本号，避免生成包含路径字符的发布文件名。
if ($normalizedVersion -notmatch '^[0-9][0-9A-Za-z.+-]*$') {
    throw "版本号格式无效：$Version"
}

# 检查本地 Docker Buildx 是否可用于构建 Linux 产物。
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未找到 Docker，请先安装 Docker Desktop 或 Docker Engine。"
}
docker buildx version | Out-Null

$architectures = if ($Arch -eq "all") { @("amd64", "arm64") } else { @($Arch) }
New-Item -ItemType Directory -Force -Path $dist, $releaseBuild | Out-Null
$releaseFiles = [Collections.Generic.List[string]]::new()

Push-Location $root
try {
    foreach ($currentArch in $architectures) {
        $artifactDir = Join-Path $releaseBuild $currentArch
        if (Test-Path $artifactDir) {
            Remove-Item -LiteralPath $artifactDir -Recurse -Force
        }
        New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
        $dockerArtifactDir = $artifactDir.Replace("\", "/")

        # 使用目标 Linux 平台构建动态库和独立助手，并导出到本地目录。
        docker buildx build `
            --platform "linux/$currentArch" `
            --target release-artifacts `
            --build-arg "VERSION=$normalizedVersion" `
            --output "type=local,dest=$dockerArtifactDir" `
            --file Dockerfile.cpaimage .
        if ($LASTEXITCODE -ne 0) {
            throw "linux/$currentArch Docker Buildx 构建失败。"
        }

        $pluginPath = Join-Path $artifactDir "cpaimage.so"
        $helperPath = Join-Path $artifactDir "cpaimage-helper"
        if (-not (Test-Path $pluginPath) -or -not (Test-Path $helperPath)) {
            throw "linux/$currentArch 构建完成但未找到预期产物。"
        }

        $packageName = "cpaimage_${normalizedVersion}_linux_${currentArch}"
        $zipPath = Join-Path $dist ($packageName + ".zip")
        $tarPath = Join-Path $dist ($packageName + ".tar.gz")

        # 压缩包保持扁平结构，可直接解压到 plugins/linux/<架构> 目录。
        Compress-Archive -Path $pluginPath, $helperPath -DestinationPath $zipPath -Force
        if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
            throw "未找到 tar，无法生成 tar.gz 发布包。"
        }
        if (Test-Path $tarPath) {
            Remove-Item -LiteralPath $tarPath -Force
        }
        Push-Location $artifactDir
        try {
            tar -czf $tarPath.Replace("\", "/") cpaimage.so cpaimage-helper
            if ($LASTEXITCODE -ne 0) {
                throw "linux/$currentArch tar.gz 打包失败。"
            }
        } finally {
            Pop-Location
        }

        $releaseFiles.Add($zipPath)
        $releaseFiles.Add($tarPath)
    }

    # 为本次生成的全部压缩包写入统一 SHA-256 校验文件。
    $hashLines = $releaseFiles | ForEach-Object {
        $hash = Get-FileHash -Algorithm SHA256 $_
        "$($hash.Hash.ToLower())  $([IO.Path]::GetFileName($_))"
    }
    $checksumPath = Join-Path $dist "SHA256SUMS"
    [IO.File]::WriteAllLines($checksumPath, $hashLines)
    Write-Output "Linux 发布包构建完成：$dist"
} finally {
    Pop-Location
}
