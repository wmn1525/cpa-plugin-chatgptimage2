package main

import (
	"encoding/base64"
	"fmt"
	"sync"
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

// TestTokenExpiresBefore 验证已过期、寿命不足和不透明 Token 的筛选语义。
func TestTokenExpiresBefore(t *testing.T) {
	now := time.Now()
	shortToken := testJWT(now.Add(time.Minute).Unix())
	longToken := testJWT(now.Add(time.Hour).Unix())
	if !tokenExpiresBefore(shortToken, now.Add(2*time.Minute)) {
		t.Fatal("寿命不足的 Token 应被跳过")
	}
	if tokenExpiresBefore(longToken, now.Add(2*time.Minute)) {
		t.Fatal("寿命充足的 Token 不应被跳过")
	}
	if tokenExpiresBefore("opaque-token", now.Add(2*time.Minute)) {
		t.Fatal("无法解析 exp 的 Token 应交给上游验证")
	}
}

// testJWT 生成仅供过期时间单元测试使用的无签名 JWT。
func testJWT(expiresAt int64) string {
	payload := fmt.Sprintf(`{"exp":%d}`, expiresAt)
	return "header." + base64.RawURLEncoding.EncodeToString([]byte(payload)) + ".signature"
}

// TestReconfigureKeepsActiveHelper 验证重复配置和普通配置变化不会重启助手。
func TestReconfigureKeepsActiveHelper(t *testing.T) {
	cfg := runtimeConfig{BaseURL: defaultBaseURL, RequestTimeout: time.Minute, HelperPath: "helper-a"}
	plugin := newImagePlugin(cfg)
	original := plugin.helper
	plugin.reconfigure(cfg)
	if plugin.helper != original {
		t.Fatal("相同配置不应替换助手")
	}
	cfg.ProxyURL = "http://127.0.0.1:8080"
	plugin.reconfigure(cfg)
	if plugin.helper != original {
		t.Fatal("助手路径未变化时不应替换助手")
	}
	select {
	case <-original.closed:
		t.Fatal("普通配置变化不应关闭助手")
	default:
	}
}

// TestReconfigureDrainsOldHelper 验证路径热切换等待在途请求释放旧助手。
func TestReconfigureDrainsOldHelper(t *testing.T) {
	cfg := runtimeConfig{BaseURL: defaultBaseURL, RequestTimeout: time.Minute, HelperPath: "helper-a"}
	plugin := newImagePlugin(cfg)
	_, oldClient, release := plugin.acquireHelper()
	oldSlot := plugin.helper
	cfg.HelperPath = "helper-b"
	plugin.reconfigure(cfg)
	if plugin.helper == oldSlot || plugin.helper.client == oldClient {
		t.Fatal("助手路径变化后新请求应切换到新助手")
	}
	select {
	case <-oldSlot.closed:
		t.Fatal("在途请求完成前不应关闭旧助手")
	default:
	}
	release()
	select {
	case <-oldSlot.closed:
	case <-time.After(time.Second):
		t.Fatal("在途请求完成后旧助手未关闭")
	}
}

// TestConcurrentReconfigureLeases 验证高并发租约与多次热切换不会提前关闭助手。
func TestConcurrentReconfigureLeases(t *testing.T) {
	cfg := runtimeConfig{BaseURL: defaultBaseURL, RequestTimeout: time.Minute, HelperPath: "helper-0"}
	plugin := newImagePlugin(cfg)
	const workers = 64
	start := make(chan struct{})
	releases := make(chan func(), workers)
	var wait sync.WaitGroup
	for index := 0; index < workers; index++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			<-start
			_, _, release := plugin.acquireHelper()
			releases <- release
		}()
	}
	close(start)
	wait.Wait()
	for index := 1; index <= 4; index++ {
		cfg.HelperPath = fmt.Sprintf("helper-%d", index)
		plugin.reconfigure(cfg)
	}
	for index := 0; index < workers; index++ {
		(<-releases)()
	}
	plugin.shutdown()
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
