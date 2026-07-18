package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"
)

type helperCredential struct {
	AuthIndex   string `json:"auth_index"`
	AccessToken string `json:"access_token"`
	Email       string `json:"email,omitempty"`
	ProxyURL    string `json:"proxy_url,omitempty"`
}

// loadCredentials 从 CPA 读取所有可用于网页生图的 Codex OAuth 凭证。
func loadCredentials(minValidity time.Duration) ([]helperCredential, error) {
	entries, err := listHostAuths()
	if err != nil {
		return nil, err
	}
	sort.SliceStable(entries, func(i, j int) bool { return entries[i].Priority > entries[j].Priority })
	credentials := make([]helperCredential, 0, len(entries))
	diagnostics := make([]string, 0, len(entries))
	expiring := 0
	for _, entry := range entries {
		diagnostics = append(diagnostics, fmt.Sprintf("%s(disabled=%t,unavailable=%t,runtime=%t)", entry.Type, entry.Disabled, entry.Unavailable, entry.RuntimeOnly))
		if entry.Disabled || entry.Unavailable || entry.RuntimeOnly || !strings.EqualFold(entry.Type, "codex") {
			continue
		}
		stored, errGet := getHostAuth(entry.AuthIndex)
		if errGet != nil {
			continue
		}
		var data map[string]any
		if errJSON := json.Unmarshal(stored.JSON, &data); errJSON != nil {
			continue
		}
		token, _ := data["access_token"].(string)
		if strings.TrimSpace(token) == "" {
			continue
		}
		if tokenExpiresBefore(token, time.Now().Add(minValidity+30*time.Second)) {
			expiring++
			continue
		}
		proxyURL, _ := data["proxy_url"].(string)
		credentials = append(credentials, helperCredential{
			AuthIndex:   entry.AuthIndex,
			AccessToken: strings.TrimSpace(token),
			Email:       entry.Email,
			ProxyURL:    strings.TrimSpace(proxyURL),
		})
	}
	if len(credentials) == 0 {
		return nil, fmt.Errorf("CPA 中没有寿命足够的 Codex OAuth 凭证；宿主返回 %d 条记录，%d 条即将过期: %s",
			len(entries), expiring, strings.Join(diagnostics, ","))
	}
	return credentials, nil
}

// tokenExpiresBefore 只解析 JWT 的 exp，用于避免选择将在请求截止前过期的凭证。
func tokenExpiresBefore(token string, deadline time.Time) bool {
	parts := strings.Split(token, ".")
	if len(parts) < 2 {
		return false
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		payload, err = base64.URLEncoding.DecodeString(parts[1])
	}
	if err != nil {
		return false
	}
	var claims struct {
		ExpiresAt float64 `json:"exp"`
	}
	if err := json.Unmarshal(payload, &claims); err != nil || claims.ExpiresAt <= 0 {
		return false
	}
	return !time.Unix(int64(claims.ExpiresAt), 0).After(deadline)
}
