# MCP 沙盒（sandbox）

工具库的落地载体。每个 MCP server 以 **Docker 容器** 隔离运行，注册信息存
`mcp_servers` 表，由后端 `app/mcp.py` 生命周期管理（wsl docker 驱动）。

## 定位

- 沙盒只存放 **候选工具**；进工具库（N7）前仍要走三关校验 + 用户审批，沿用既有铁律。
- MCP 是协议不是服务（私有化部署无影响）；对外以 MCP server 暴露，内部保留版本追踪。

## 使用

1. 「MCP 管理」页 → 新建：填名称、镜像（`image`）、传输方式（http/stdio）、端口、命令。
2. 启动 → 后端执行 `docker run -d --name rag_mcp_<id> -p <port> <image> <command>`。
3. 停止 → `docker stop` + `rm -f`。
4. 状态 → `docker inspect` 实时读取。

## 目录约定

```
sandbox/
├── README.md          # 本文件
└── Dockerfile.example # 示例：如何把一个 MCP server 打成可隔离运行的镜像
```

## 示例 Dockerfile

见 `Dockerfile.example`：以 Python MCP server 为例，说明 stdio / http 两种挂载方式。
