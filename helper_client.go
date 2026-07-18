package main

import (
	"bufio"
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"os/exec"
	"sync"
	"sync/atomic"
)

const maxHelperFrame = 512 << 20

type helperRequest struct {
	ID      uint64 `json:"id"`
	Method  string `json:"method"`
	Payload any    `json:"payload"`
}

type helperResponse struct {
	ID     uint64          `json:"id"`
	OK     bool            `json:"ok"`
	Result json.RawMessage `json:"result,omitempty"`
	Error  *rpcError       `json:"error,omitempty"`
}

type helperClient struct {
	path    string
	mu      sync.Mutex
	writeMu sync.Mutex
	cmd     *exec.Cmd
	stdin   io.WriteCloser
	stdout  *bufio.Reader
	pending map[uint64]chan helperResponse
	nextID  atomic.Uint64
}

// newHelperClient 创建可自动重启的助手客户端。
func newHelperClient(path string) *helperClient {
	return &helperClient{path: path, pending: make(map[uint64]chan helperResponse)}
}

// Call 并发发送请求并等待对应 ID 的助手响应。
func (c *helperClient) Call(ctx context.Context, method string, payload any, result any) error {
	if err := c.ensureStarted(); err != nil {
		return err
	}
	id := c.nextID.Add(1)
	responseCh := make(chan helperResponse, 1)
	c.mu.Lock()
	c.pending[id] = responseCh
	stdin := c.stdin
	c.mu.Unlock()
	request := helperRequest{ID: id, Method: method, Payload: payload}
	if err := c.writeFrame(stdin, request); err != nil {
		c.failProcess(err)
		return err
	}
	select {
	case response := <-responseCh:
		if !response.OK {
			if response.Error != nil {
				return &statusError{status: response.Error.HTTPStatus, message: response.Error.Message}
			}
			return fmt.Errorf("助手请求失败")
		}
		if result != nil {
			return json.Unmarshal(response.Result, result)
		}
		return nil
	case <-ctx.Done():
		c.mu.Lock()
		delete(c.pending, id)
		c.mu.Unlock()
		c.cancelCall(id)
		return ctx.Err()
	}
}

// cancelCall 通知助手关闭超时请求仍占用的网页会话。
func (c *helperClient) cancelCall(requestID uint64) {
	c.mu.Lock()
	stdin := c.stdin
	c.mu.Unlock()
	if stdin == nil {
		return
	}
	id := c.nextID.Add(1)
	_ = c.writeFrame(stdin, helperRequest{ID: id, Method: "cancel", Payload: map[string]uint64{"request_id": requestID}})
}

// Restart 关闭当前助手，使下一次调用使用新配置启动。
func (c *helperClient) Restart() { c.Close() }

// Close 关闭助手进程并结束所有等待请求。
func (c *helperClient) Close() {
	c.mu.Lock()
	cmd := c.cmd
	c.cmd = nil
	stdin := c.stdin
	c.stdin = nil
	c.stdout = nil
	pending := c.pending
	c.pending = make(map[uint64]chan helperResponse)
	c.mu.Unlock()
	if stdin != nil {
		_ = stdin.Close()
	}
	if cmd != nil && cmd.Process != nil {
		_ = cmd.Process.Kill()
		_, _ = cmd.Process.Wait()
	}
	for _, ch := range pending {
		ch <- helperResponse{OK: false, Error: &rpcError{Message: "助手进程已关闭", HTTPStatus: 502}}
	}
}

// ensureStarted 确保助手进程已启动并完成管道初始化。
func (c *helperClient) ensureStarted() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.cmd != nil {
		return nil
	}
	cmd := exec.Command(c.path, "--stdio")
	configureHiddenProcess(cmd)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("启动助手失败 %s: %w", c.path, err)
	}
	c.cmd = cmd
	c.stdin = stdin
	c.stdout = bufio.NewReaderSize(stdout, 64*1024)
	go c.readLoop(cmd, c.stdout)
	return nil
}

// writeFrame 写入一个大端长度前缀 JSON 帧。
func (c *helperClient) writeFrame(writer io.Writer, value any) error {
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	header := make([]byte, 4)
	binary.BigEndian.PutUint32(header, uint32(len(raw)))
	if _, err := writer.Write(header); err != nil {
		return err
	}
	_, err = writer.Write(raw)
	return err
}

// readLoop 持续读取助手响应并投递给对应调用者。
func (c *helperClient) readLoop(cmd *exec.Cmd, reader *bufio.Reader) {
	for {
		header := make([]byte, 4)
		if _, err := io.ReadFull(reader, header); err != nil {
			c.failProcess(err)
			return
		}
		length := binary.BigEndian.Uint32(header)
		if length == 0 || length > maxHelperFrame {
			c.failProcess(fmt.Errorf("助手响应帧长度无效: %d", length))
			return
		}
		raw := make([]byte, length)
		if _, err := io.ReadFull(reader, raw); err != nil {
			c.failProcess(err)
			return
		}
		var response helperResponse
		if err := json.Unmarshal(raw, &response); err != nil {
			continue
		}
		c.mu.Lock()
		ch := c.pending[response.ID]
		delete(c.pending, response.ID)
		c.mu.Unlock()
		if ch != nil {
			ch <- response
		}
	}
}

// failProcess 标记助手失效并通知所有正在等待的请求。
func (c *helperClient) failProcess(err error) {
	c.mu.Lock()
	cmd := c.cmd
	c.cmd = nil
	c.stdin = nil
	c.stdout = nil
	pending := c.pending
	c.pending = make(map[uint64]chan helperResponse)
	c.mu.Unlock()
	if cmd != nil && cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
	for _, ch := range pending {
		ch <- helperResponse{OK: false, Error: &rpcError{Message: "助手进程异常: " + err.Error(), HTTPStatus: 502}}
	}
}
