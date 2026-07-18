package main

import (
	"encoding/json"
	"fmt"

	"github.com/router-for-me/CLIProxyAPI/v7/sdk/pluginabi"
	"github.com/router-for-me/CLIProxyAPI/v7/sdk/pluginapi"
)

type hostEnvelope struct {
	OK     bool            `json:"ok"`
	Result json.RawMessage `json:"result,omitempty"`
	Error  *rpcError       `json:"error,omitempty"`
}

type authListResponse struct {
	Files []pluginapi.HostAuthFileEntry `json:"files"`
}

// callHostJSON 调用 CPA 宿主方法并解码统一响应信封。
func callHostJSON(method string, request any, result any) error {
	rawRequest, err := json.Marshal(request)
	if err != nil {
		return fmt.Errorf("编码宿主请求失败: %w", err)
	}
	rawResponse, err := callHost(method, rawRequest)
	if err != nil {
		return err
	}
	var envelope hostEnvelope
	if err := json.Unmarshal(rawResponse, &envelope); err != nil {
		return fmt.Errorf("解码宿主响应失败: %w", err)
	}
	if !envelope.OK {
		if envelope.Error != nil {
			return fmt.Errorf("宿主调用失败: %s", envelope.Error.Message)
		}
		return fmt.Errorf("宿主调用失败")
	}
	if result == nil || len(envelope.Result) == 0 {
		return nil
	}
	if err := json.Unmarshal(envelope.Result, result); err != nil {
		return fmt.Errorf("解码宿主结果失败: %w", err)
	}
	return nil
}

// listHostAuths 获取 CPA 当前运行时凭证列表。
func listHostAuths() ([]pluginapi.HostAuthFileEntry, error) {
	var response authListResponse
	if err := callHostJSON(pluginabi.MethodHostAuthList, map[string]any{}, &response); err != nil {
		return nil, err
	}
	return response.Files, nil
}

// getHostAuth 获取指定 CPA 凭证对应的物理 JSON。
func getHostAuth(authIndex string) (pluginapi.HostAuthGetResponse, error) {
	var response pluginapi.HostAuthGetResponse
	err := callHostJSON(pluginabi.MethodHostAuthGet, pluginapi.HostAuthGetRequest{AuthIndex: authIndex}, &response)
	return response, err
}
