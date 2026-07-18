package main

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

type helperCredential struct {
	AuthIndex   string `json:"auth_index"`
	AccessToken string `json:"access_token"`
	Email       string `json:"email,omitempty"`
	ProxyURL    string `json:"proxy_url,omitempty"`
}

// loadCredentials 从 CPA 读取所有可用于网页生图的 Codex OAuth 凭证。
func loadCredentials() ([]helperCredential, error) {
	entries, err := listHostAuths()
	if err != nil {
		return nil, err
	}
	sort.SliceStable(entries, func(i, j int) bool { return entries[i].Priority > entries[j].Priority })
	credentials := make([]helperCredential, 0, len(entries))
	diagnostics := make([]string, 0, len(entries))
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
		proxyURL, _ := data["proxy_url"].(string)
		credentials = append(credentials, helperCredential{
			AuthIndex:   entry.AuthIndex,
			AccessToken: strings.TrimSpace(token),
			Email:       entry.Email,
			ProxyURL:    strings.TrimSpace(proxyURL),
		})
	}
	if len(credentials) == 0 {
		return nil, fmt.Errorf("CPA 中没有可用的 Codex OAuth 凭证；宿主返回 %d 条记录: %s", len(entries), strings.Join(diagnostics, ","))
	}
	return credentials, nil
}
