# CPA ChatGPT Web Image Plugin

[English](README.md) | [简体中文](README_CN.md)

A [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) plugin that intercepts `gpt-image-2` Images API requests and generates images through the ChatGPT web `picture_v2` flow with Codex OAuth credentials already managed by CPA.

Repository: https://github.com/wmn1525/cpa-plugin-chatgptimage2

## Features

- Intercepts only `POST /v1/images/generations` and `POST /v1/images/edits` requests using `gpt-image-2`.
- Reuses enabled Codex OAuth credentials from CPA through `host.auth.list/get`.
- Does not copy credential files, persist access tokens, or log sensitive tokens.
- Implements the ChatGPT web image flow with `curl_cffi`, browser fingerprints, Sentinel/PoW, uploads, SSE parsing, polling, image downloads, and conversation cleanup.
- Supports JSON and multipart image edits, data URLs, remote images, multiple input images, and masks.
- Supports `n=1-4`, ordered parallel generation, credential rotation, and in-memory cooldowns.
- Supports non-streaming responses and CPA-compatible SSE output.
- Does not deploy or require a separate chatgpt2api service.
- Does not call CPA's native Codex image-generation endpoint.

## Components

- `cpaimage.dll` / `cpaimage.so`: CPA dynamic-library plugin for routing, credential access, and helper process management.
- `cpaimage-helper.exe` / `cpaimage-helper`: standalone helper containing the ChatGPT web request implementation and Python runtime dependencies.

## Supported Platforms

| Platform | Architecture | Plugin | Helper |
|---|---|---|---|
| Windows | amd64 | `cpaimage.dll` | `cpaimage-helper.exe` |
| Linux | amd64 | `cpaimage.so` | `cpaimage-helper` |
| Linux | arm64 | `cpaimage.so` | `cpaimage-helper` |

CPA v7.2.86 or later is required. Linux release binaries target Debian Bookworm-compatible glibc environments, including the official CPA Docker image.

## Configuration

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
      helper_path: "/CLIProxyAPI/plugins/linux/amd64/cpaimage-helper"
```

For Linux arm64, change `helper_path` to:

```yaml
helper_path: "/CLIProxyAPI/plugins/linux/arm64/cpaimage-helper"
```

For Windows, use an absolute path such as:

```yaml
helper_path: "C:/CLIProxyAPI/plugins/windows/amd64/cpaimage-helper.exe"
```

### Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `true` | Enables or disables this plugin. |
| `priority` | integer | `100` | CPA plugin routing priority. |
| `base_url` | string | `https://chatgpt.com` | ChatGPT web upstream or local mock service. |
| `request_timeout` | duration | `20m` | Total timeout for one image request. |
| `proxy_url` | string | empty | HTTP, HTTPS, or SOCKS5 proxy. |
| `cf_cookies` | string | empty | Optional Cloudflare cookie string. |
| `cleanup_conversation` | boolean | `true` | Removes the generated web conversation after success. |
| `helper_path` | string | auto-detect | Absolute or working-directory-relative helper path. |

A credential-level `proxy_url` takes precedence over the global plugin proxy.

## Linux Docker Installation from GitHub Releases

Release page: https://github.com/wmn1525/cpa-plugin-chatgptimage2/releases

Release assets are named as follows:

```text
cpaimage_<version>_linux_amd64.tar.gz
cpaimage_<version>_linux_amd64.zip
cpaimage_<version>_linux_arm64.tar.gz
cpaimage_<version>_linux_arm64.zip
SHA256SUMS
```

### 1. Mount a Persistent Plugin Directory

Add the plugin volume to the existing CPA Compose service while keeping its current ports and volumes:

```yaml
services:
  cli-proxy-api:
    volumes:
      - ./config.yaml:/CLIProxyAPI/config.yaml
      - ./auths:/root/.cli-proxy-api
      - ./logs:/CLIProxyAPI/logs
      - ./plugins:/CLIProxyAPI/plugins
```

### 2. Download and Extract the Plugin

```bash
REPO="wmn1525/cpa-plugin-chatgptimage2"
VERSION="0.1.0"

case "$(uname -m)" in
  x86_64) ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) echo "Unsupported architecture"; exit 1 ;;
esac

ASSET="cpaimage_${VERSION}_linux_${ARCH}.tar.gz"
mkdir -p "./plugins/linux/${ARCH}"

curl -fL \
  "https://github.com/${REPO}/releases/download/v${VERSION}/${ASSET}" \
  -o "/tmp/${ASSET}"
curl -fL \
  "https://github.com/${REPO}/releases/download/v${VERSION}/SHA256SUMS" \
  -o /tmp/SHA256SUMS

cd /tmp
grep " ${ASSET}$" SHA256SUMS | sha256sum -c -
cd -

tar -xzf "/tmp/${ASSET}" -C "./plugins/linux/${ARCH}"
chmod 0755 "./plugins/linux/${ARCH}/cpaimage.so" \
           "./plugins/linux/${ARCH}/cpaimage-helper"
```

### 3. Restart CPA

Merge the configuration shown above into `config.yaml`, then recreate the CPA container:

```bash
docker compose up -d --force-recreate cli-proxy-api
docker compose logs cli-proxy-api 2>&1 | grep -E "cpaimage|plugin registered|pluginhost"
```

A successful load includes:

```text
pluginhost: plugin registered plugin_id=cpaimage
```

Do not use `docker cp` as the permanent installation method unless the container path is backed by a volume. Files copied only into a container disappear when it is recreated.

## Windows Installation

From a Windows release directory, run:

```powershell
.\install.ps1 -CpaDir "C:\CLIProxyAPI"
```

The files are installed to:

```text
C:\CLIProxyAPI\plugins\windows\amd64\cpaimage.dll
C:\CLIProxyAPI\plugins\windows\amd64\cpaimage-helper.exe
```

Merge the Windows `helper_path` configuration into CPA and restart it.

## Credential Management

Upload Codex OAuth JSON through the existing CPA management page or Management API. No separate credential pool is required.

The credential must contain at least:

```json
{
  "type": "codex",
  "access_token": "..."
}
```

The plugin discovers newly uploaded or refreshed credentials on subsequent requests. Authentication failures, rate limits, and temporary network errors trigger credential rotation and an in-memory cooldown.

## API Examples

### Image Generation

```bash
curl http://127.0.0.1:8317/v1/images/generations \
  -H "Authorization: Bearer YOUR_CPA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "A cat floating in space",
    "n": 1,
    "response_format": "b64_json"
  }'
```

### Image Edit

```bash
curl http://127.0.0.1:8317/v1/images/edits \
  -H "Authorization: Bearer YOUR_CPA_API_KEY" \
  -F "model=gpt-image-2" \
  -F "prompt=Change the scene to a cyberpunk night" \
  -F "image=@input.png"
```

### Streaming Request

```json
{
  "model": "gpt-image-2",
  "prompt": "Tokyo streets on a rainy night",
  "stream": true
}
```

The web flow normally emits one final `image.generation.result` SSE event after the image is complete. Partial-image progress is not guaranteed.

## Local Builds

### Linux Release Packages with Docker Buildx

Docker Desktop or Docker Engine with Buildx is required:

```powershell
.\scripts\build-linux-release.ps1 -Version "0.1.0" -Arch "all"
```

Build one architecture only:

```powershell
.\scripts\build-linux-release.ps1 -Version "0.1.0" -Arch "amd64"
```

Packages and `SHA256SUMS` are written to `dist/`.

### Windows Build

Requirements:

- Go 1.26
- MinGW-w64 GCC
- Python 3.12
- Dependencies from `requirements-helper.txt`

```powershell
py -3.12 -m pip install -r requirements-helper.txt
.\scripts\build.ps1 -Version "0.1.0"
```

## GitHub Releases

The workflow at `.github/workflows/release.yml` builds Linux amd64 and arm64 archives. Push a `v*` tag to create a GitHub Release:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Manual workflow runs create downloadable Actions artifacts but do not create a formal Release.

## Tests

```powershell
go test ./...
go vet ./...
py -3.12 -m unittest -v tests.test_helper tests.test_helper_exe
```

The integration test starts a real CPA v7.2.86 process with a local mock ChatGPT service:

```powershell
.\scripts\integration-test.ps1
```

## Acknowledgements

The ChatGPT web request sequence, Sentinel/PoW implementation, upload flow, and result parsing were developed with reference to [basketikun/chatgpt2api](https://github.com/basketikun/chatgpt2api). Thanks to its contributors for the original research and implementation.

## Limitations

- Only `gpt-image-2` Images API requests are intercepted.
- Chat Completions, Responses, `gpt-image-1.5`, xAI, and custom image models are not intercepted.
- Forced Arkose challenges return an explicit error; automatic Arkose solving is not included.
- If all CPA credentials fail, the plugin returns an OpenAI-compatible error and does not fall back to native CPA image generation.

## Uninstall

Remove `cpaimage.dll`/`cpaimage.so` and the matching helper from the platform plugin directory, remove `plugins.configs.cpaimage` from CPA configuration, and restart CPA.

Additional third-party sources and notices are documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
