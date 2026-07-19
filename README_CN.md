# CPA ChatGPT 网页生图插件

[English](README.md) | [简体中文](README_CN.md)

这是一个 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 插件，用于劫持 `gpt-image-2` Images API 请求，并使用 CPA 已管理的 Codex OAuth 凭证执行 ChatGPT 网页端 `picture_v2` 生图链路。

仓库地址：https://github.com/wmn1525/cpa-plugin-chatgptimage2

## 功能

- 仅劫持使用 `gpt-image-2` 的 `POST /v1/images/generations` 和 `POST /v1/images/edits`。
- 通过 `host.auth.list/get` 复用 CPA 中启用的 Codex OAuth 凭证。
- 不复制凭证文件、不持久化 access token、不在日志中输出敏感 Token。
- 使用 `curl_cffi` 实现浏览器指纹、Sentinel/PoW、图片上传、SSE 解析、轮询、下载和会话清理。
- 支持 JSON 与 multipart 图片编辑、data URL、远程图片、多个输入图片和 mask。
- 支持 `n=1-4`、保持顺序的并行生成、凭证轮换和内存冷却。
- 支持非流式响应和 CPA 可直接透传的 SSE 输出。
- 不需要单独部署 chatgpt2api 服务。
- 不调用 CPA 原生 Codex 生图接口。

## 组件

- `cpaimage.dll` / `cpaimage.so`：自包含 CPA 动态库；首次请求时会把内嵌助手校验并释放到用户缓存目录。
- `cpaimage-helper.exe` / `cpaimage-helper`：仅源码构建和高级覆盖时使用，商店安装不需要此文件。

## 支持平台

| 平台 | 架构 | 商店安装文件 |
|---|---|---|
| Windows | amd64 | `cpaimage.dll` |
| Linux | amd64 | `cpaimage.so` |
| Linux | arm64 | `cpaimage.so` |

要求 CPA v7.2.86 或更新版本。Linux 发布包面向与 Debian Bookworm glibc 兼容的环境，包括 CPA 官方 Docker 镜像。

## 配置

```yaml
plugins:
  enabled: true
  dir: "/CLIProxyAPI/plugins"
  configs:
    cpaimage:
      enabled: true
      priority: 100
      base_url: "https://chatgpt.com"
      request_timeout: "20m"
      proxy_url: ""
      cf_cookies: ""
      cleanup_conversation: true
```

普通用户无需配置 `helper_path`。只有调试外部助手时才设置绝对路径，例如：

```yaml
helper_path: "C:/CLIProxyAPI/plugins/windows/amd64/cpaimage-helper.exe"
```

### 配置字段

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | boolean | `true` | 启用或停用插件。 |
| `priority` | integer | `100` | CPA 插件路由优先级。 |
| `base_url` | string | `https://chatgpt.com` | ChatGPT 网页上游或本地模拟服务地址。 |
| `request_timeout` | duration | `20m` | 单次生图请求总超时。 |
| `proxy_url` | string | 空 | HTTP、HTTPS 或 SOCKS5 代理。 |
| `cf_cookies` | string | 空 | 可选 Cloudflare Cookie 字符串。 |
| `cleanup_conversation` | boolean | `true` | 成功后清理对应网页会话。 |
| `helper_path` | string | 内嵌助手 | 高级覆盖项：外部助手的绝对路径或工作目录相对路径。 |

凭证自身的 `proxy_url` 优先于插件全局代理。

## CPA 插件商店安装

插件进入官方商店后，在 CPA 管理页面搜索 `cpaimage` 并点击安装即可。安装器会写入 `cpaimage-v0.1.8.dll` 或 `cpaimage-v0.1.8.so`；安装完成后完全重启 CPA。若商店暂未显示该条目，请使用下面的 GitHub Release 安装方式。

## 从 GitHub Release 安装到 Linux Docker

Release 页面：https://github.com/wmn1525/cpa-plugin-chatgptimage2/releases

发布附件名称：

```text
cpaimage_0.1.8_windows_amd64.zip
cpaimage_0.1.8_linux_amd64.zip
cpaimage_0.1.8_linux_arm64.zip
checksums.txt
```

ZIP 根目录只有动态库；助手已嵌入。Linux `tar.gz` 仍可作为手动安装附件。

### 1. 挂载持久化插件目录

在现有 CPA Compose 服务中增加插件目录挂载，其他端口和卷保持不变：

```yaml
services:
  cli-proxy-api:
    volumes:
      - ./config.yaml:/CLIProxyAPI/config.yaml
      - ./auths:/root/.cli-proxy-api
      - ./logs:/CLIProxyAPI/logs
      - ./plugins:/CLIProxyAPI/plugins
```

### 2. 下载并解压插件

```bash
REPO="wmn1525/cpa-plugin-chatgptimage2"
VERSION="0.1.8"

case "$(uname -m)" in
  x86_64) ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) echo "不支持的架构"; exit 1 ;;
esac

ASSET="cpaimage_${VERSION}_linux_${ARCH}.tar.gz"
mkdir -p "./plugins/linux/${ARCH}"

curl -fL \
  "https://github.com/${REPO}/releases/download/v${VERSION}/${ASSET}" \
  -o "/tmp/${ASSET}"
curl -fL \
  "https://github.com/${REPO}/releases/download/v${VERSION}/checksums.txt" \
  -o /tmp/checksums.txt

cd /tmp
grep " ${ASSET}$" checksums.txt | sha256sum -c -
cd -

rm -f "./plugins/linux/${ARCH}"/cpaimage-v*.so \
      "./plugins/linux/${ARCH}/cpaimage-helper"
tar -xzf "/tmp/${ASSET}" -C "./plugins/linux/${ARCH}"
chmod 0755 "./plugins/linux/${ARCH}/cpaimage.so"
```

### 3. 重启 CPA

把前面的插件配置合并到 `config.yaml`，然后重建 CPA 容器：

```bash
docker compose up -d --force-recreate cli-proxy-api
docker compose logs cli-proxy-api 2>&1 | grep -E "cpaimage|plugin registered|pluginhost"
```

正常加载日志包含：

```text
pluginhost: plugin registered plugin_id=cpaimage
```

除非容器内插件路径已经挂载为数据卷，否则不要把 `docker cp` 作为长期安装方式。只复制到容器内部的文件会在容器重建后消失。

## Windows 安装

从同一个 GitHub Release 下载并解压 `cpaimage_0.1.8_windows_amd64.zip`。ZIP 根目录只有 `cpaimage.dll`，可在解压目录执行：

```powershell
$target = "C:\CLIProxyAPI\plugins\windows\amd64"
New-Item -ItemType Directory -Force $target | Out-Null
Get-ChildItem $target -Filter "cpaimage-v*.dll" -File | Remove-Item -Force
Remove-Item "$target\cpaimage-helper.exe" -Force -ErrorAction SilentlyContinue
Copy-Item ".\cpaimage.dll" $target -Force
```

文件会安装到：

```text
C:\CLIProxyAPI\plugins\windows\amd64\cpaimage.dll
```

仓库中的 `scripts/install.ps1` 可执行相同操作。必须先清理残留版本文件，再完全重启 CPA；无需配置 `helper_path`。

## 凭证管理

通过 CPA 原有管理页面或管理 API 上传 Codex OAuth JSON，不需要建立单独号池。

凭证至少包含：

```json
{
  "type": "codex",
  "access_token": "..."
}
```

插件会在后续请求中自动发现新增或刷新的凭证。认证失败、限流和临时网络错误会触发换号及内存冷却。

插件会解析 JWT 的 `exp`，只选择有效期能够覆盖 `request_timeout` 并额外保留 30 秒余量的凭证；无法解析 `exp` 的不透明 Token 仍交给上游验证。凭证在生成、轮询或下载过程中返回 401 时，助手会立即释放该凭证并换号。若当前请求快照中的凭证全部失效，DLL 会重新调用一次 CPA `auth.list/get`，让同一请求使用 CPA 刚刷新的 Token 重试。旧 Token 与新 Token 使用不同的内存冷却键。

上传凭证不会重启助手。CPA 重复下发配置时继续使用当前助手；只有助手路径真正变化时，新请求才切换到新进程，旧进程会在全部在途生成完成后关闭。凭证忙碌时会立即跳过并尝试其他可用账号。

## API 示例

### 文生图

```bash
curl http://127.0.0.1:8317/v1/images/generations \
  -H "Authorization: Bearer 你的_CPA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "一只漂浮在太空中的猫",
    "n": 1,
    "response_format": "b64_json"
  }'
```

### 图片编辑

```bash
curl http://127.0.0.1:8317/v1/images/edits \
  -H "Authorization: Bearer 你的_CPA_API_KEY" \
  -F "model=gpt-image-2" \
  -F "prompt=把场景改成赛博朋克夜景" \
  -F "image=@input.png"
```

### 流式请求

```json
{
  "model": "gpt-image-2",
  "prompt": "雨夜东京街头",
  "stream": true
}
```

网页链路通常在图片完成后输出一个最终 `image.generation.result` SSE 事件，不保证提供局部图片进度。

## 本地构建

### 使用 Docker Buildx 构建 Linux Release

需要 Docker Desktop 或带 Buildx 的 Docker Engine：

```powershell
.\scripts\build-linux-release.ps1 -Version "0.1.8" -Arch "all"
```

只构建一个架构：

```powershell
.\scripts\build-linux-release.ps1 -Version "0.1.8" -Arch "amd64"
```

压缩包和 `checksums.txt` 会写入 `dist/`。

### Windows 构建

构建要求：

- Go 1.26
- MinGW-w64 GCC
- Python 3.12
- `requirements-helper.txt` 中的依赖

```powershell
py -3.12 -m pip install -r requirements-helper.txt
.\scripts\build.ps1 -Version "0.1.8"
```

## GitHub Releases

`.github/workflows/release.yml` 会构建 Windows amd64、Linux amd64 和 Linux arm64 压缩包。推送发布分支后开始构建，工作流会在全部附件校验通过后自行创建 Git 标签和 GitHub Release：

```bash
git push origin HEAD:release/v0.1.8
```

不要自行推送 `v0.1.8` 标签，也不要手工创建同名 Release。工作流会先创建草稿，上传并校验全部附件，所有平台构建成功后再同时发布 Release 和标签。

手动运行工作流只生成可下载的 Actions Artifacts，不创建正式 Release。发布完成后可以删除临时的 `release/v0.1.8` 分支。

## 测试

```powershell
go test -race .
go vet .
py -3.12 -m unittest -v tests.test_helper tests.test_helper_exe
```

集成测试会启动真实 CPA v7.2.86 进程和本地模拟 ChatGPT 服务：

```powershell
.\scripts\integration-test.ps1
```

集成测试固定执行总计 100 个图片请求，并验证 100/100 成功、配置热重载、凭证热上传及凭证使用中返回 401 后自动换号。也可以对已运行的 CPA 单独执行压测：

```powershell
.\scripts\load-test.ps1 `
  -BaseUrl "http://127.0.0.1:8317/v1" `
  -ApiKey "你的_CPA_API_KEY" `
  -Total 100 -Concurrency 20
```

## 致谢

本项目的 ChatGPT 网页请求顺序、Sentinel/PoW 实现、上传链路和结果解析参考了 [basketikun/chatgpt2api](https://github.com/basketikun/chatgpt2api)。感谢该项目贡献者提供的研究和实现。

## 限制

- 只劫持 `gpt-image-2` Images API 请求。
- 不劫持 Chat Completions、Responses、`gpt-image-1.5`、xAI 和自定义图片模型。
- 遇到 Arkose 强制验证时返回明确错误，不包含自动 Arkose 解题。
- 全部 CPA 凭证失败时返回 OpenAI 兼容错误，不回退 CPA 原生生图。

## 卸载

从对应平台的插件目录删除 `cpaimage.dll`/`cpaimage.so`、`cpaimage-v*.dll`/`cpaimage-v*.so` 和旧助手文件，移除 CPA 配置中的 `plugins.configs.cpaimage`，然后重启 CPA。

本项目使用 [MIT License](LICENSE)。其他第三方来源和声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
