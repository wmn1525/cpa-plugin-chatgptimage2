package main

import (
	"os"
	"path/filepath"
	"testing"
)

// TestMaterializeEmbeddedHelper 验证助手按内容摘要稳定释放并修复损坏缓存。
func TestMaterializeEmbeddedHelper(t *testing.T) {
	root := t.TempDir()
	payload := []byte("embedded-helper-test")
	first, errFirst := materializeEmbeddedHelper(root, payload)
	if errFirst != nil {
		t.Fatalf("materializeEmbeddedHelper() error = %v", errFirst)
	}
	second, errSecond := materializeEmbeddedHelper(root, payload)
	if errSecond != nil || second != first {
		t.Fatalf("第二次释放路径/错误 = %q/%v, want %q/nil", second, errSecond, first)
	}
	if data, errRead := os.ReadFile(first); errRead != nil || string(data) != string(payload) {
		t.Fatalf("助手内容 = %q, error = %v", data, errRead)
	}
	if errWrite := os.WriteFile(first, []byte("damaged"), 0o600); errWrite != nil {
		t.Fatalf("破坏测试缓存失败: %v", errWrite)
	}
	repaired, errRepair := materializeEmbeddedHelper(root, payload)
	if errRepair != nil || repaired != first {
		t.Fatalf("修复路径/错误 = %q/%v", repaired, errRepair)
	}
	if data, errRead := os.ReadFile(repaired); errRead != nil || string(data) != string(payload) {
		t.Fatalf("修复后助手内容 = %q, error = %v", data, errRead)
	}
}

// TestResolveHelperPathExplicit 验证显式 helper_path 始终优先于内嵌助手。
func TestResolveHelperPathExplicit(t *testing.T) {
	configured := filepath.Join(t.TempDir(), "custom-helper")
	resolved, errResolve := resolveHelperPath(configured)
	if errResolve != nil {
		t.Fatalf("resolveHelperPath() error = %v", errResolve)
	}
	absolute, _ := filepath.Abs(configured)
	if resolved != absolute {
		t.Fatalf("resolveHelperPath() = %q, want %q", resolved, absolute)
	}
}
