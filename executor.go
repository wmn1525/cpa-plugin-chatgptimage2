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
		BaseURL:             cfg.BaseURL,
		TimeoutSeconds:      int(cfg.RequestTimeout / time.Second),
		ProxyURL:            cfg.ProxyURL,
		CFCookies:           cfg.CFCookies,
		CleanupConversation: cfg.CleanupConversation,
	}
	var response helperExecutionResult
	var previousCredentials []helperCredential
	var firstAuthError error
	for attempt := 0; attempt < 2; attempt++ {
		remaining := remainingTimeout(ctx)
		if remaining <= 0 {
			return executionResult{}, context.DeadlineExceeded
		}
		credentials, err := loadCredentials(remaining)
		if err != nil {
			if attempt > 0 {
				return executionResult{}, firstAuthError
			}
			return executionResult{}, err
		}
		if attempt > 0 && !hasRefreshedCredential(previousCredentials, credentials) {
			return executionResult{}, firstAuthError
		}
		payload.Credentials = credentials
		payload.TimeoutSeconds = remainingTimeoutSeconds(ctx)
		if payload.TimeoutSeconds <= 0 {
			return executionResult{}, context.DeadlineExceeded
		}
		err = helper.Call(ctx, "images", payload, &response)
		if err == nil {
			break
		}
		status, isStatus := err.(*statusError)
		if attempt == 0 && isStatus && status.status == http.StatusUnauthorized {
			previousCredentials = credentials
			firstAuthError = err
			continue
		}
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

// hasRefreshedCredential 检查重新读取的快照是否包含旧快照中没有的新 Token。
func hasRefreshedCredential(previous []helperCredential, current []helperCredential) bool {
	oldTokens := make(map[string]struct{}, len(previous))
	for _, credential := range previous {
		oldTokens[credential.AccessToken] = struct{}{}
	}
	for _, credential := range current {
		if _, existed := oldTokens[credential.AccessToken]; !existed {
			return true
		}
	}
	return false
}

// remainingTimeoutSeconds 返回上下文剩余时间并向上取整到整秒。
func remainingTimeoutSeconds(ctx context.Context) int {
	remaining := remainingTimeout(ctx)
	if remaining <= 0 {
		return 0
	}
	return int((remaining + time.Second - 1) / time.Second)
}

// remainingTimeout 返回上下文截止时间前的剩余时长。
func remainingTimeout(ctx context.Context) time.Duration {
	deadline, ok := ctx.Deadline()
	if !ok {
		return 0
	}
	return time.Until(deadline)
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
