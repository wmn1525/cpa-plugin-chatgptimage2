package main

import (
	"testing"
	"time"

	"github.com/router-for-me/CLIProxyAPI/v7/sdk/pluginapi"
)

// TestParseConfigDefaults 验证插件默认配置与显式配置。
func TestParseConfigDefaults(t *testing.T) {
	cfg, err := parseConfig(nil)
	if err != nil {
		t.Fatalf("parseConfig() error = %v", err)
	}
	if cfg.BaseURL != defaultBaseURL || cfg.RequestTimeout != 20*time.Minute || !cfg.CleanupConversation {
		t.Fatalf("默认配置异常: %#v", cfg)
	}
	cfg, err = parseConfig([]byte("base_url: http://127.0.0.1:9000/\nrequest_timeout: 30s\ncleanup_conversation: false\n"))
	if err != nil {
		t.Fatalf("parseConfig(custom) error = %v", err)
	}
	if cfg.BaseURL != "http://127.0.0.1:9000" || cfg.RequestTimeout != 30*time.Second || cfg.CleanupConversation {
		t.Fatalf("显式配置异常: %#v", cfg)
	}
}

// TestRouteModel 验证仅目标模型和 Images API 路径被劫持。
func TestRouteModel(t *testing.T) {
	plugin := newImagePlugin(runtimeConfig{})
	tests := []struct {
		model   string
		path    string
		handled bool
	}{
		{"gpt-image-2", "/v1/images/generations", true},
		{"GPT-IMAGE-2", "/v1/images/edits", true},
		{"gpt-image-1.5", "/v1/images/generations", false},
		{"gpt-image-2", "/v1/responses", false},
	}
	for _, test := range tests {
		response := plugin.routeModel(pluginapi.ModelRouteRequest{
			RequestedModel: test.model,
			Metadata:       map[string]any{"request_path": test.path},
		})
		if response.Handled != test.handled {
			t.Fatalf("routeModel(%s, %s).Handled = %v", test.model, test.path, response.Handled)
		}
	}
}

// TestRegistration 验证插件注册能力与格式声明。
func TestRegistration(t *testing.T) {
	registration := registrationInfo()
	if !registration.Capabilities.ModelRouter || !registration.Capabilities.Executor {
		t.Fatal("缺少路由或执行器能力")
	}
	if registration.Capabilities.ExecutorModelScope != pluginapi.ExecutorModelScopeStatic {
		t.Fatalf("ExecutorModelScope = %s", registration.Capabilities.ExecutorModelScope)
	}
	if len(registration.Capabilities.ExecutorInputFormats) != 1 || registration.Capabilities.ExecutorInputFormats[0] != "openai-image" {
		t.Fatalf("输入格式异常: %#v", registration.Capabilities.ExecutorInputFormats)
	}
}

// TestMetadataString 验证执行元数据字符串读取。
func TestMetadataString(t *testing.T) {
	if value := metadataString(map[string]any{"request_path": "/v1/images/generations"}, "request_path"); value != "/v1/images/generations" {
		t.Fatalf("metadataString() = %q", value)
	}
}
