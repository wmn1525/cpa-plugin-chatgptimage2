# 第三方说明

本项目的 ChatGPT 网页生图请求顺序、Sentinel/PoW 算法、Turnstile 解释逻辑、图片上传与结果解析流程参考了以下 MIT 项目：

- [basketikun/chatgpt2api](https://github.com/basketikun/chatgpt2api)

运行时助手使用以下第三方组件：

- `curl_cffi`：浏览器 TLS/HTTP 指纹与网络请求。
- `Pillow`：图片尺寸读取和 mask alpha 合成。
- `PyInstaller`：构建独立 Windows 助手程序。

