package main

/*
#include <stdint.h>
#include <stdlib.h>

typedef struct { void* ptr; size_t len; } cliproxy_buffer;
typedef int (*cliproxy_host_call_fn)(void*, const char*, const uint8_t*, size_t, cliproxy_buffer*);
typedef void (*cliproxy_host_free_fn)(void*, size_t);
typedef struct {
	uint32_t abi_version;
	void* host_ctx;
	cliproxy_host_call_fn call;
	cliproxy_host_free_fn free_buffer;
} cliproxy_host_api;
typedef int (*cliproxy_plugin_call_fn)(char*, uint8_t*, size_t, cliproxy_buffer*);
typedef void (*cliproxy_plugin_free_fn)(void*, size_t);
typedef void (*cliproxy_plugin_shutdown_fn)(void);
typedef struct {
	uint32_t abi_version;
	cliproxy_plugin_call_fn call;
	cliproxy_plugin_free_fn free_buffer;
	cliproxy_plugin_shutdown_fn shutdown;
} cliproxy_plugin_api;

extern int CPAImagePluginCall(char*, uint8_t*, size_t, cliproxy_buffer*);
extern void CPAImagePluginFree(void*, size_t);
extern void CPAImagePluginShutdown(void);

static int cpaimage_call_host(cliproxy_host_api* api, const char* method, const uint8_t* request, size_t request_len, cliproxy_buffer* response) {
	return api->call(api->host_ctx, method, request, request_len, response);
}
static void cpaimage_free_host_buffer(cliproxy_host_api* api, void* ptr, size_t len) {
	api->free_buffer(ptr, len);
}
*/
import "C"

import (
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"unsafe"

	"github.com/router-for-me/CLIProxyAPI/v7/sdk/pluginabi"
	"github.com/router-for-me/CLIProxyAPI/v7/sdk/pluginapi"
)

type rpcEnvelope struct {
	OK     bool            `json:"ok"`
	Result json.RawMessage `json:"result,omitempty"`
	Error  *rpcError       `json:"error,omitempty"`
}

type rpcError struct {
	Code       string `json:"code"`
	Message    string `json:"message"`
	Retryable  bool   `json:"retryable,omitempty"`
	HTTPStatus int    `json:"http_status,omitempty"`
}

var abiState = struct {
	sync.RWMutex
	host   *C.cliproxy_host_api
	plugin *imagePlugin
}{}

func main() {}

//export cliproxy_plugin_init
func cliproxy_plugin_init(host *C.cliproxy_host_api, plugin *C.cliproxy_plugin_api) C.int {
	if host == nil || plugin == nil {
		return 1
	}
	abiState.Lock()
	abiState.host = host
	abiState.Unlock()
	plugin.abi_version = C.uint32_t(pluginabi.ABIVersion)
	plugin.call = C.cliproxy_plugin_call_fn(C.CPAImagePluginCall)
	plugin.free_buffer = C.cliproxy_plugin_free_fn(C.CPAImagePluginFree)
	plugin.shutdown = C.cliproxy_plugin_shutdown_fn(C.CPAImagePluginShutdown)
	return 0
}

//export CPAImagePluginCall
func CPAImagePluginCall(method *C.char, request *C.uint8_t, requestLen C.size_t, response *C.cliproxy_buffer) C.int {
	if response != nil {
		response.ptr = nil
		response.len = 0
	}
	if method == nil {
		writeABIResponse(response, errorEnvelope("invalid_method", "method is required", http.StatusBadRequest))
		return 0
	}
	var rawRequest []byte
	if request != nil && requestLen > 0 {
		rawRequest = C.GoBytes(unsafe.Pointer(request), C.int(requestLen))
	}
	rawResponse, err := handleABIMethod(C.GoString(method), rawRequest)
	if err != nil {
		status := http.StatusInternalServerError
		if value, ok := err.(interface{ StatusCode() int }); ok {
			status = value.StatusCode()
		}
		rawResponse = errorEnvelope("plugin_error", err.Error(), status)
	}
	writeABIResponse(response, rawResponse)
	return 0
}

//export CPAImagePluginFree
func CPAImagePluginFree(ptr unsafe.Pointer, length C.size_t) {
	if ptr != nil {
		C.free(ptr)
	}
}

//export CPAImagePluginShutdown
func CPAImagePluginShutdown() {
	abiState.Lock()
	plugin := abiState.plugin
	abiState.plugin = nil
	abiState.host = nil
	abiState.Unlock()
	if plugin != nil {
		plugin.shutdown()
	}
}

// handleABIMethod 分发 CPA 插件 RPC 方法。
func handleABIMethod(method string, request []byte) ([]byte, error) {
	if method == pluginabi.MethodPluginRegister || method == pluginabi.MethodPluginReconfigure {
		lifecycle, err := decodeRequest[lifecycleRequest](request)
		if err != nil {
			return nil, err
		}
		cfg, err := parseConfig(lifecycle.ConfigYAML)
		if err != nil {
			return nil, err
		}
		abiState.Lock()
		if abiState.plugin == nil {
			abiState.plugin = newImagePlugin(cfg)
		} else {
			abiState.plugin.reconfigure(cfg)
		}
		abiState.Unlock()
		return okEnvelope(registrationInfo())
	}
	abiState.RLock()
	plugin := abiState.plugin
	abiState.RUnlock()
	if plugin == nil {
		return nil, fmt.Errorf("插件尚未注册")
	}
	switch method {
	case pluginabi.MethodModelRoute:
		req, err := decodeRequest[rpcModelRouteRequest](request)
		if err != nil {
			return nil, err
		}
		return okEnvelope(plugin.routeModel(req.ModelRouteRequest))
	case pluginabi.MethodExecutorIdentifier:
		return okEnvelope(identifierResponse{Identifier: pluginID})
	case pluginabi.MethodExecutorExecute:
		req, err := decodeRequest[rpcExecutorRequest](request)
		if err != nil {
			return nil, err
		}
		result, err := plugin.execute(req)
		if err != nil {
			return nil, err
		}
		return okEnvelope(result)
	case pluginabi.MethodExecutorExecuteStream:
		req, err := decodeRequest[rpcExecutorRequest](request)
		if err != nil {
			return nil, err
		}
		result, err := plugin.executeStream(req)
		if err != nil {
			return nil, err
		}
		return okEnvelope(result)
	case pluginabi.MethodExecutorCountTokens:
		return okEnvelope(pluginapi.ExecutorResponse{Payload: []byte(`{"total_tokens":0}`)})
	case pluginabi.MethodExecutorHTTPRequest:
		return okEnvelope(pluginapi.ExecutorHTTPResponse{StatusCode: http.StatusNotImplemented, Body: []byte(`{"error":"not implemented"}`)})
	default:
		return errorEnvelope("unknown_method", "unknown method: "+method, http.StatusBadRequest), nil
	}
}

// okEnvelope 编码成功的 CPA RPC 信封。
func okEnvelope(value any) ([]byte, error) {
	result, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	return json.Marshal(rpcEnvelope{OK: true, Result: result})
}

// errorEnvelope 编码失败的 CPA RPC 信封。
func errorEnvelope(code, message string, status int) []byte {
	raw, _ := json.Marshal(rpcEnvelope{OK: false, Error: &rpcError{Code: code, Message: message, HTTPStatus: status}})
	return raw
}

// writeABIResponse 把 Go 字节写入由 CPA 释放的 C 缓冲区。
func writeABIResponse(response *C.cliproxy_buffer, raw []byte) {
	if response == nil || len(raw) == 0 {
		return
	}
	ptr := C.CBytes(raw)
	if ptr == nil {
		return
	}
	response.ptr = ptr
	response.len = C.size_t(len(raw))
}

// callHost 调用 CPA 提供的宿主函数表。
func callHost(method string, payload []byte) ([]byte, error) {
	abiState.RLock()
	host := abiState.host
	abiState.RUnlock()
	if host == nil {
		return nil, fmt.Errorf("宿主回调不可用")
	}
	cMethod := C.CString(method)
	defer C.free(unsafe.Pointer(cMethod))
	var cPayload unsafe.Pointer
	if len(payload) > 0 {
		cPayload = C.CBytes(payload)
		defer C.free(cPayload)
	}
	var response C.cliproxy_buffer
	rc := C.cpaimage_call_host(host, cMethod, (*C.uint8_t)(cPayload), C.size_t(len(payload)), &response)
	var out []byte
	if response.ptr != nil && response.len > 0 {
		out = C.GoBytes(response.ptr, C.int(response.len))
	}
	if response.ptr != nil {
		C.cpaimage_free_host_buffer(host, response.ptr, response.len)
	}
	if rc != 0 {
		return nil, fmt.Errorf("宿主回调 %s 返回 %d", method, int(rc))
	}
	return out, nil
}
