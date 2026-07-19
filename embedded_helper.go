package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
)

// embeddedHelperPath 把发布构建内的助手释放到内容寻址缓存目录。
func embeddedHelperPath() (string, error) {
	if len(embeddedHelperPayload) == 0 {
		return "", nil
	}
	var roots []string
	if cacheDir, errCache := os.UserCacheDir(); errCache == nil && cacheDir != "" {
		roots = append(roots, cacheDir)
	}
	if tempDir := os.TempDir(); tempDir != "" && !containsPath(roots, tempDir) {
		roots = append(roots, tempDir)
	}
	var errs []error
	for _, root := range roots {
		path, errExtract := materializeEmbeddedHelper(root, embeddedHelperPayload)
		if errExtract == nil {
			return path, nil
		}
		errs = append(errs, errExtract)
	}
	return "", fmt.Errorf("释放内嵌助手失败，可通过 helper_path 指定外部助手: %w", errors.Join(errs...))
}

// materializeEmbeddedHelper 原子写入助手并验证最终文件摘要。
func materializeEmbeddedHelper(cacheRoot string, payload []byte) (string, error) {
	digest := sha256.Sum256(payload)
	digestText := hex.EncodeToString(digest[:])
	targetDir := filepath.Join(cacheRoot, "cpaimage", "helpers", digestText)
	targetPath := filepath.Join(targetDir, helperExecutableName())
	if helperFileMatches(targetPath, digest) {
		if errChmod := os.Chmod(targetPath, 0o700); errChmod != nil {
			return "", fmt.Errorf("设置助手权限失败: %w", errChmod)
		}
		return targetPath, nil
	}
	if errMkdir := os.MkdirAll(targetDir, 0o700); errMkdir != nil {
		return "", fmt.Errorf("创建助手缓存目录失败: %w", errMkdir)
	}
	temp, errTemp := os.CreateTemp(targetDir, ".cpaimage-helper-*")
	if errTemp != nil {
		return "", fmt.Errorf("创建助手临时文件失败: %w", errTemp)
	}
	tempPath := temp.Name()
	closed := false
	defer func() {
		if !closed {
			_ = temp.Close()
		}
		_ = os.Remove(tempPath)
	}()
	if errChmod := temp.Chmod(0o700); errChmod != nil {
		return "", fmt.Errorf("设置助手临时文件权限失败: %w", errChmod)
	}
	if _, errWrite := io.Copy(temp, bytes.NewReader(payload)); errWrite != nil {
		return "", fmt.Errorf("写入助手失败: %w", errWrite)
	}
	if errSync := temp.Sync(); errSync != nil {
		return "", fmt.Errorf("同步助手失败: %w", errSync)
	}
	if errClose := temp.Close(); errClose != nil {
		return "", fmt.Errorf("关闭助手临时文件失败: %w", errClose)
	}
	closed = true
	if errRename := os.Rename(tempPath, targetPath); errRename != nil {
		if helperFileMatches(targetPath, digest) {
			return targetPath, nil
		}
		if errRemove := os.Remove(targetPath); errRemove != nil && !errors.Is(errRemove, os.ErrNotExist) {
			return "", fmt.Errorf("移除损坏助手失败: %w", errRemove)
		}
		if errRetry := os.Rename(tempPath, targetPath); errRetry != nil {
			return "", fmt.Errorf("安装助手失败: %w", errRetry)
		}
	}
	if !helperFileMatches(targetPath, digest) {
		return "", fmt.Errorf("助手写入后摘要校验失败")
	}
	return targetPath, nil
}

// helperFileMatches 检查现有缓存是否为预期的普通文件和摘要。
func helperFileMatches(path string, digest [sha256.Size]byte) bool {
	info, errStat := os.Stat(path)
	if errStat != nil || !info.Mode().IsRegular() {
		return false
	}
	file, errOpen := os.Open(path)
	if errOpen != nil {
		return false
	}
	defer file.Close()
	hasher := sha256.New()
	if _, errCopy := io.Copy(hasher, file); errCopy != nil {
		return false
	}
	return bytes.Equal(hasher.Sum(nil), digest[:])
}

// helperExecutableName 返回当前平台的助手文件名。
func helperExecutableName() string {
	if runtime.GOOS == "windows" {
		return "cpaimage-helper.exe"
	}
	return "cpaimage-helper"
}

// containsPath 检查路径列表是否已包含同一清理路径。
func containsPath(paths []string, candidate string) bool {
	candidate = filepath.Clean(candidate)
	for _, path := range paths {
		if filepath.Clean(path) == candidate {
			return true
		}
	}
	return false
}
