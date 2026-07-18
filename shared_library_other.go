//go:build !windows

package main

// sharedLibraryPath 在非 Windows 构建中返回空路径。
func sharedLibraryPath() string { return "" }

// helperExecutablePath 返回非 Windows 测试使用的默认助手路径。
func helperExecutablePath() string { return "cpaimage-helper" }
