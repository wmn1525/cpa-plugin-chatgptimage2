//go:build windows

package main

import (
	"os/exec"
	"syscall"
)

// configureHiddenProcess 配置 Windows 助手进程隐藏窗口。
func configureHiddenProcess(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
}
