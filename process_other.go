//go:build !windows

package main

import "os/exec"

// configureHiddenProcess 在非 Windows 平台保持默认进程属性。
func configureHiddenProcess(cmd *exec.Cmd) {}
