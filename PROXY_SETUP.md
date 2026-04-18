# Vless 代理配置指南

## 快速开始

### 1. 配置代理

创建 `.env` 文件：

```bash
# 启用代理功能
PROXY_ENABLED=true

# 订阅URL（支持多个，用逗号分隔）
VLESS_SUBSCRIPTION_URLS=https://example.com/subscription

# 节点匹配规则
VLESS_SUBSCRIPTION_PATTERNS=CF优选-电信

# 启动时自动刷新
VLESS_AUTO_REFRESH_ON_START=true

# 节点存储文件
VLESS_STORAGE_FILE=vless_nodes.json
```

### 2. 启动服务

```bash
# 启动服务（代理会根据配置自动启用/禁用）
python start_server.py
```

### 3. 验证代理

```bash
# 查看代理统计
curl http://localhost:8000/v1/proxy/stats

# 查看可用节点
curl http://localhost:8000/v1/proxy/nodes
```

## 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `PROXY_ENABLED` | 是否启用代理功能 | `false` |
| `VLESS_SUBSCRIPTION_URLS` | 订阅URL（多个用逗号分隔） | - |
| `VLESS_SUBSCRIPTION_PATTERNS` | 节点匹配规则 | `CF优选-电信` |
| `VLESS_AUTO_REFRESH_ON_START` | 启动时自动刷新订阅 | `true` |
| `VLESS_STORAGE_FILE` | 节点存储文件 | `vless_nodes.json` |

### 管理后台配置

访问 `http://localhost:8000/admin`，进入"设置"页面：

1. **启用代理** - 开启/关闭代理功能
2. **订阅 URL** - 填写订阅链接（多个用逗号分隔）
3. **节点匹配规则** - 填写匹配规则（如 `CF优选-电信`）

## API 端点

### 获取代理统计
```bash
GET /v1/proxy/stats
```

响应示例：
```json
{
  "enabled": true,
  "stats": {
    "pattern": "CF优选-电信",
    "current_pattern": {
      "total": 10,
      "available": 8
    }
  }
}
```

### 刷新订阅
```bash
POST /v1/proxy/refresh
Content-Type: application/json

{
  "test_nodes": true
}
```

响应示例：
```json
{
  "success": true,
  "result": {
    "fetched_nodes": 10,
    "added": 2,
    "updated": 8,
    "tested": 10,
    "available": 8
  }
}
```

### 获取节点列表
```bash
GET /v1/proxy/nodes?pattern=CF优选-电信&only_available=true
```

响应示例：
```json
{
  "total": 8,
  "nodes": [
    {
      "identifier": "1.2.3.4:443",
      "name": "CF优选-电信-01",
      "address": "1.2.3.4",
      "port": 443,
      "network": "tcp",
      "tls": true,
      "is_available": true,
      "fail_count": 0,
      "success_count": 5,
      "average_latency": 150.5
    }
  ]
}
```

### 测试节点
```bash
POST /v1/proxy/test
Content-Type: application/json

{
  "pattern": "CF优选-电信",
  "max_concurrent": 10
}
```

响应示例：
```json
{
  "success": true,
  "summary": {
    "total": 10,
    "success": 8,
    "failed": 2,
    "success_rate": 0.8,
    "latency": {
      "avg": 200.5,
      "min": 100.2,
      "max": 500.1
    }
  },
  "results": [...]
}
```

## 工作流程

```
1. 服务启动
   ↓
2. 检查 PROXY_ENABLED
   ↓ (如果启用)
3. 从订阅URL获取节点
   ↓
4. 按规则筛选节点（如 CF优选-电信）
   ↓
5. 测试节点可用性
   ↓
6. 存储可用节点到本地 (vless_nodes.json)
   ↓
7. API调用时随机使用可用节点
   ↓
8. 使用后标记节点结果（成功/失败）
```

## 节点选择策略

- **随机选择**：从可用节点中随机选择
- **故障转移**：节点失败3次后自动标记为不可用
- **延迟优先**：优先选择延迟低的节点

## 配置文件示例

### 多订阅源

```bash
VLESS_SUBSCRIPTION_URLS=https://sub1.com/api,https://sub2.com/api
VLESS_SUBSCRIPTION_PATTERNS=CF优选-电信,CF优选-移动
```

### 多规则匹配

```bash
# 匹配电信或移动的节点
VLESS_SUBSCRIPTION_PATTERNS=电信,移动
```

### 正则匹配

```bash
# 使用正则表达式
VLESS_SUBSCRIPTION_PATTERNS=CF.*电信.*
```

## 故障排查

### 代理未启用

检查 `.env` 文件：
```bash
PROXY_ENABLED=true
```

### 代理池未初始化

检查订阅URL：
```bash
echo $VLESS_SUBSCRIPTION_URLS
```

### 节点获取失败

检查订阅URL：
```bash
curl -v "你的订阅URL"
```

### 节点测试失败

检查网络连接：
```bash
# 测试Google连通性
curl -I https://www.google.com
```

## 注意事项

1. **订阅隐私**：订阅URL包含敏感信息，请勿泄露
2. **节点更新**：建议定期调用 `/v1/proxy/refresh` 更新节点
3. **存储文件**：`vless_nodes.json` 包含节点信息，建议定期备份
4. **并发测试**：大量节点测试时，请适当调整 `max_concurrent`
