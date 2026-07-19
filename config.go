package main

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

const defaultBaseURL = "https://chatgpt.com"

type pluginConfig struct {
	BaseURL             string `yaml:"base_url"`
	RequestTimeout      string `yaml:"request_timeout"`
	ProxyURL            string `yaml:"proxy_url"`
	CFCookies           string `yaml:"cf_cookies"`
	CleanupConversation *bool  `yaml:"cleanup_conversation"`
	HelperPath          string `yaml:"helper_path"`
}

type runtimeConfig struct {
	BaseURL             string
	RequestTimeout      time.Duration
	ProxyURL            string
	CFCookies           string
	CleanupConversation bool
	HelperPath          string
}

// parseConfig 解析并校验插件 YAML 配置。
func parseConfig(raw []byte) (runtimeConfig, error) {
	cfg := pluginConfig{}
	if len(raw) > 0 {
		if err := yaml.Unmarshal(raw, &cfg); err != nil {
			return runtimeConfig{}, fmt.Errorf("解析插件配置失败: %w", err)
		}
	}
	baseURL := strings.TrimRight(strings.TrimSpace(cfg.BaseURL), "/")
	if baseURL == "" {
		baseURL = defaultBaseURL
	}
	timeout := 20 * time.Minute
	if value := strings.TrimSpace(cfg.RequestTimeout); value != "" {
		parsed, err := time.ParseDuration(value)
		if err != nil || parsed <= 0 {
			return runtimeConfig{}, fmt.Errorf("request_timeout 必须是正数 Go duration")
		}
		timeout = parsed
	}
	cleanup := true
	if cfg.CleanupConversation != nil {
		cleanup = *cfg.CleanupConversation
	}
	helperPath, errHelper := resolveHelperPath(strings.TrimSpace(cfg.HelperPath))
	if errHelper != nil {
		return runtimeConfig{}, errHelper
	}
	return runtimeConfig{
		BaseURL:             baseURL,
		RequestTimeout:      timeout,
		ProxyURL:            strings.TrimSpace(cfg.ProxyURL),
		CFCookies:           strings.TrimSpace(cfg.CFCookies),
		CleanupConversation: cleanup,
		HelperPath:          helperPath,
	}, nil
}

// resolveHelperPath 解析助手路径并兼容 CPA 的 DLL 临时影子加载目录。
func resolveHelperPath(configured string) (string, error) {
	if configured != "" {
		if filepath.IsAbs(configured) {
			return filepath.Clean(configured), nil
		}
		if absolute, err := filepath.Abs(configured); err == nil {
			return absolute, nil
		}
	}
	helperName := helperExecutableName()
	candidates := []string{
		helperExecutablePath(),
		filepath.Join("plugins", runtime.GOOS, runtime.GOARCH, helperName),
		filepath.Join("plugins", helperName),
		helperName,
	}
	for _, candidate := range candidates {
		if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
			if absolute, errAbs := filepath.Abs(candidate); errAbs == nil {
				return absolute, nil
			}
		}
	}
	embeddedPath, errEmbedded := embeddedHelperPath()
	if errEmbedded != nil {
		return "", errEmbedded
	}
	if embeddedPath != "" {
		return embeddedPath, nil
	}
	return candidates[0], nil
}
