package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"

	"github.com/router-for-me/CLIProxyAPI/v7/sdk/pluginabi"
	"github.com/router-for-me/CLIProxyAPI/v7/sdk/pluginapi"
)

const (
	pluginName          = "cpaimage"
	pluginID            = "cpaimage"
	pluginRepositoryURL = "https://github.com/wmn1525/cpa-plugin-chatgptimage2"
)

var pluginVersion = "dev"

type imagePlugin struct {
	mu     sync.RWMutex
	config runtimeConfig
	helper *helperSlot
}

type helperSlot struct {
	client    *helperClient
	active    sync.WaitGroup
	closeOnce sync.Once
	closed    chan struct{}
}

type lifecycleRequest struct {
	ConfigYAML []byte `json:"config_yaml"`
}

type rpcModelRouteRequest struct {
	pluginapi.ModelRouteRequest
	HostCallbackID string `json:"host_callback_id,omitempty"`
}

type rpcExecutorRequest struct {
	pluginapi.ExecutorRequest
	StreamID       string `json:"stream_id,omitempty"`
	HostCallbackID string `json:"host_callback_id,omitempty"`
}

type registration struct {
	SchemaVersion uint32             `json:"schema_version"`
	Metadata      pluginapi.Metadata `json:"metadata"`
	Capabilities  capabilities       `json:"capabilities"`
}

type capabilities struct {
	ModelRouter           bool                         `json:"model_router"`
	Executor              bool                         `json:"executor"`
	ExecutorModelScope    pluginapi.ExecutorModelScope `json:"executor_model_scope"`
	ExecutorInputFormats  []string                     `json:"executor_input_formats,omitempty"`
	ExecutorOutputFormats []string                     `json:"executor_output_formats,omitempty"`
}

type identifierResponse struct {
	Identifier string `json:"identifier"`
}

type streamResponse struct {
	Headers http.Header                     `json:"headers,omitempty"`
	Chunks  []pluginapi.ExecutorStreamChunk `json:"chunks,omitempty"`
}

// newImagePlugin 创建插件运行实例并定位同目录助手程序。
func newImagePlugin(cfg runtimeConfig) *imagePlugin {
	return &imagePlugin{config: cfg, helper: newHelperSlot(cfg.HelperPath)}
}

// newHelperSlot 创建可追踪在途请求的助手实例。
func newHelperSlot(path string) *helperSlot {
	return &helperSlot{client: newHelperClient(path), closed: make(chan struct{})}
}

// retire 在全部在途请求完成后关闭已替换的助手。
func (s *helperSlot) retire() {
	if s == nil {
		return
	}
	go func() {
		s.active.Wait()
		s.close()
	}()
}

// close 只关闭一次助手并通知测试和生命周期等待方。
func (s *helperSlot) close() {
	if s == nil {
		return
	}
	s.closeOnce.Do(func() {
		s.client.Close()
		close(s.closed)
	})
}

// acquireHelper 获取配置快照和助手租约，避免热切换中断请求。
func (p *imagePlugin) acquireHelper() (runtimeConfig, *helperClient, func()) {
	p.mu.RLock()
	slot := p.helper
	if slot == nil {
		p.mu.RUnlock()
		return runtimeConfig{}, nil, func() {}
	}
	slot.active.Add(1)
	cfg := p.config
	p.mu.RUnlock()
	return cfg, slot.client, slot.active.Done
}

// reconfigure 更新配置；只有助手路径变化时才无中断切换进程。
func (p *imagePlugin) reconfigure(cfg runtimeConfig) {
	p.mu.Lock()
	if p.config == cfg && p.helper != nil {
		p.mu.Unlock()
		return
	}
	oldHelper := p.helper
	if oldHelper == nil || oldHelper.client.path != cfg.HelperPath {
		p.helper = newHelperSlot(cfg.HelperPath)
	}
	newHelper := p.helper
	p.config = cfg
	p.mu.Unlock()
	if oldHelper != nil && oldHelper != newHelper {
		oldHelper.retire()
	}
}

// shutdown 关闭插件持有的助手进程。
func (p *imagePlugin) shutdown() {
	p.mu.Lock()
	helper := p.helper
	p.helper = nil
	p.mu.Unlock()
	if helper != nil {
		helper.close()
	}
}

// routeModel 仅劫持两个 Images API 上的 gpt-image-2 请求。
func (p *imagePlugin) routeModel(req pluginapi.ModelRouteRequest) pluginapi.ModelRouteResponse {
	if !strings.EqualFold(strings.TrimSpace(req.RequestedModel), "gpt-image-2") {
		return pluginapi.ModelRouteResponse{}
	}
	path, _ := req.Metadata["request_path"].(string)
	if path != "/v1/images/generations" && path != "/v1/images/edits" {
		return pluginapi.ModelRouteResponse{}
	}
	return pluginapi.ModelRouteResponse{Handled: true, TargetKind: pluginapi.ModelRouteTargetSelf, Reason: "使用 ChatGPT 网页生图链路"}
}

// execute 调用助手完成一次非流式图片请求。
func (p *imagePlugin) execute(req rpcExecutorRequest) (pluginapi.ExecutorResponse, error) {
	result, err := p.executeHelper(req)
	if err != nil {
		return pluginapi.ExecutorResponse{}, err
	}
	return pluginapi.ExecutorResponse{Payload: result.Body, Headers: result.Headers}, nil
}

// executeStream 调用助手并把最终结果包装为 CPA 可透传的 SSE 数据。
func (p *imagePlugin) executeStream(req rpcExecutorRequest) (streamResponse, error) {
	result, err := p.executeHelper(req)
	if err != nil {
		return streamResponse{}, err
	}
	return streamResponse{Headers: result.Headers, Chunks: []pluginapi.ExecutorStreamChunk{{Payload: result.Body}}}, nil
}

// registrationInfo 返回 CPA 注册所需的元数据与能力声明。
func registrationInfo() registration {
	return registration{
		SchemaVersion: pluginabi.SchemaVersion,
		Metadata: pluginapi.Metadata{
			Name:             "CPA ChatGPT 网页生图",
			Version:          pluginVersion,
			Author:           "wmn1525",
			GitHubRepository: pluginRepositoryURL,
			ConfigFields: []pluginapi.ConfigField{
				{Name: "base_url", Type: pluginapi.ConfigFieldTypeString, Description: "ChatGPT 网页上游地址。"},
				{Name: "request_timeout", Type: pluginapi.ConfigFieldTypeString, Description: "单次生图总超时，例如 20m。"},
				{Name: "proxy_url", Type: pluginapi.ConfigFieldTypeString, Description: "可选 HTTP/HTTPS/SOCKS5 代理。"},
				{Name: "cf_cookies", Type: pluginapi.ConfigFieldTypeString, Description: "可选 Cloudflare Cookie 字符串。"},
				{Name: "cleanup_conversation", Type: pluginapi.ConfigFieldTypeBoolean, Description: "成功后删除网页生图会话。"},
				{Name: "helper_path", Type: pluginapi.ConfigFieldTypeString, Description: "外部助手可执行文件的绝对或工作目录相对路径。"},
			},
		},
		Capabilities: capabilities{
			ModelRouter:           true,
			Executor:              true,
			ExecutorModelScope:    pluginapi.ExecutorModelScopeStatic,
			ExecutorInputFormats:  []string{"openai-image"},
			ExecutorOutputFormats: []string{"openai-image"},
		},
	}
}

// decodeRequest 解码指定类型的插件 RPC 请求。
func decodeRequest[T any](raw []byte) (T, error) {
	var value T
	if err := json.Unmarshal(raw, &value); err != nil {
		return value, fmt.Errorf("解码插件请求失败: %w", err)
	}
	return value, nil
}
