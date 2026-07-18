package main

import (
	"context"
	"encoding/base64"
	"fmt"
	"net/http"
	"time"
)

type helperExecutionRequest struct {
	RequestPath         string             `json:"request_path"`
	BodyBase64          string             `json:"body_base64"`
	ContentType         string             `json:"content_type"`
	Stream              bool               `json:"stream"`
	Credentials         []helperCredential `json:"credentials"`
	BaseURL             string             `json:"base_url"`
	TimeoutSeconds      int                `json:"timeout_seconds"`
	ProxyURL            string             `json:"proxy_url,omitempty"`
	CFCookies           string             `json:"cf_cookies,omitempty"`
	CleanupConversation bool               `json:"cleanup_conversation"`
}

type helperExecutionResult struct {
	StatusCode int         `json:"status_code"`
	Headers    http.Header `json:"headers"`
	BodyBase64 string      `json:"body_base64"`
}

type executionResult struct {
	StatusCode int
	Headers    http.Header
	Body       []byte
}

// executeHelper 组装凭证和原始 Images API 请求并交给助手处理。
func (p *imagePlugin) executeHelper(req rpcExecutorRequest) (executionResult, error) {
	credentials, err := loadCredentials()
	if err != nil {
		return executionResult{}, err
	}
	cfg, helper, release := p.acquireHelper()
	defer release()
	if helper == nil {
		return executionResult{}, fmt.Errorf("助手尚未初始化")
	}
	ctx, cancel := context.WithTimeout(context.Background(), cfg.RequestTimeout)
	defer cancel()
	payload := helperExecutionRequest{
		RequestPath:         metadataString(req.Metadata, "request_path"),
		BodyBase64:          base64.StdEncoding.EncodeToString(req.Payload),
		ContentType:         req.Headers.Get("Content-Type"),
		Stream:              req.Stream,
		Credentials:         credentials,
		BaseURL:             cfg.BaseURL,
		TimeoutSeconds:      int(cfg.RequestTimeout / time.Second),
		ProxyURL:            cfg.ProxyURL,
		CFCookies:           cfg.CFCookies,
		CleanupConversation: cfg.CleanupConversation,
	}
	var response helperExecutionResult
	if err := helper.Call(ctx, "images", payload, &response); err != nil {
		return executionResult{}, err
	}
	body, err := base64.StdEncoding.DecodeString(response.BodyBase64)
	if err != nil {
		return executionResult{}, fmt.Errorf("助手返回了无效响应正文: %w", err)
	}
	if response.StatusCode >= 400 {
		return executionResult{}, &statusError{status: response.StatusCode, message: string(body)}
	}
	return executionResult{StatusCode: response.StatusCode, Headers: response.Headers, Body: body}, nil
}

// metadataString 从执行器元数据读取字符串字段。
func metadataString(metadata map[string]any, key string) string {
	value, _ := metadata[key].(string)
	return value
}

type statusError struct {
	status  int
	message string
}

// Error 返回不包含凭证的上游错误描述。
func (e *statusError) Error() string { return e.message }

// StatusCode 返回 CPA 应写入客户端的 HTTP 状态码。
func (e *statusError) StatusCode() int { return e.status }
