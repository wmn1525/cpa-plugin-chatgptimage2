//go:build embedded_helper

package main

import _ "embed"

// embeddedHelperPayload 保存发布构建生成的平台助手。
//
//go:embed helper_payload/cpaimage-helper.bin
var embeddedHelperPayload []byte
