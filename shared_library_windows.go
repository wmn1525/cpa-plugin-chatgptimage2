//go:build windows

package main

/*
#include <stdint.h>
#include <stdlib.h>
#include <windows.h>

typedef struct { void* ptr; size_t len; } cliproxy_buffer;
extern int CPAImagePluginCall(char*, uint8_t*, size_t, cliproxy_buffer*);

static wchar_t* cpaimage_shared_object_path() {
	HMODULE module = NULL;
	DWORD size = MAX_PATH;
	if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
		(LPCWSTR)(void*)&CPAImagePluginCall, &module)) {
		return NULL;
	}
	for (;;) {
		wchar_t* buffer = (wchar_t*)malloc(size * sizeof(wchar_t));
		if (buffer == NULL) return NULL;
		DWORD copied = GetModuleFileNameW(module, buffer, size);
		if (copied == 0) { free(buffer); return NULL; }
		if (copied < size - 1) return buffer;
		free(buffer);
		size *= 2;
		if (size > 32768) return NULL;
	}
}
*/
import "C"

import (
	"path/filepath"
	"unsafe"

	"golang.org/x/sys/windows"
)

// sharedLibraryPath 获取当前 DLL 的绝对路径。
func sharedLibraryPath() string {
	path := C.cpaimage_shared_object_path()
	if path == nil {
		return ""
	}
	defer C.free(unsafe.Pointer(path))
	return windows.UTF16PtrToString((*uint16)(unsafe.Pointer(path)))
}

// helperExecutablePath 返回与 DLL 同目录的助手程序路径。
func helperExecutablePath() string {
	path := sharedLibraryPath()
	if path == "" {
		return "cpaimage-helper.exe"
	}
	return filepath.Join(filepath.Dir(path), "cpaimage-helper.exe")
}
