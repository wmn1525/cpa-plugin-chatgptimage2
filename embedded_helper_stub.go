//go:build !embedded_helper

package main

// embeddedHelperPayload 在开发构建中为空，测试和源码运行继续使用外部助手。
var embeddedHelperPayload []byte
