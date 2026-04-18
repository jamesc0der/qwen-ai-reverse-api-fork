让我详细查看自动注册和自动激活的逻辑：
        
            
toolName: view_files
            
status: success
          
            
filePath: e:\qwen-code-oai-proxy\qwen-code-oai-proxy\src\admin\storage\auto-register-store.ts
          
这是完整的 **自动注册 + 自动激活** 逻辑，非常详细！让我为你解析：

---

## 一、整体流程图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         自动注册流程 (Auto Register)                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. 注册 (signup)                                                           │
│     ↓                                                                       │
│  2. 等待验证邮件 (waitForVerificationEmail)  ←── IMAP 自动读取邮件          │
│     ↓                                                                       │
│  3. 激活账号 (activate)                                                     │
│     ↓                                                                       │
│  4. 登录获取 JWT (signin)                                                   │
│     ↓                                                                       │
│  5. 获取 OAuth 凭证 (getOAuthCredentials)                                   │
│     ↓                                                                       │
│  ✅ 完成 - 保存到账号池                                                      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、详细步骤解析

### 步骤 1: 注册账号 (signup)

```typescript
async function signup(email: string, name: string, password: string): Promise<{ id: string; token: string }> {
  // 密码 SHA-256 哈希
  const passwordHash = sha256(password);
  
  const resp = await fetch(`${QWEN_BASE_URL}/api/v1/auths/signup`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "source": "web",
      "version": "0.2.35",
      "User-Agent": USER_AGENT,
      "Referer": `${QWEN_BASE_URL}/auth?mode=register`,
      "Origin": QWEN_BASE_URL,
      // ... 模拟浏览器请求头
    },
    body: JSON.stringify({
      name,                          // 用户名
      email,                         // 邮箱
      password: passwordHash,        // 哈希后的密码
      agree: true,                   // 同意协议
      profile_image_url: DEFAULT_PROFILE_IMAGE,  // 默认头像
      oauth_sub: "",
      oauth_token: "",
      module: "chat",
    }),
  });
  
  return { id: data.id, token: data.token };
}
```

**请求地址**: `POST https://chat.qwen.ai/api/v1/auths/signup`

---

### 步骤 2: 等待验证邮件 (IMAP 自动读取)

这是核心自动激活逻辑！通过 IMAP 协议自动读取邮箱中的验证邮件：

```typescript
// IMAP 配置（默认使用 QQ 邮箱）
const IMAP_CONFIG: Imap.Config = {
  user: process.env.IMAP_USER || "2821396723@qq.com",
  password: process.env.IMAP_PASSWORD || "hnnztehtncghdega",  // 邮箱授权码
  host: process.env.IMAP_HOST || "imap.qq.com",
  port: parseInt(process.env.IMAP_PORT || "993"),
  tls: true,
  tlsOptions: { rejectUnauthorized: false },
};

// 等待验证邮件（带超时机制）
async function waitForVerificationEmail(
  targetEmail: string,
  timeoutMs = 180000  // 3分钟超时
): Promise<{ id: string; token: string }> {
  
  // 1. 确保 IMAP 连接
  await ensureSharedImap();
  
  return new Promise((resolve, reject) => {
    // 设置超时定时器
    const timer = setTimeout(() => {
      reject(new Error("Email verification timeout"));
    }, timeoutMs);
    
    // 添加到等待队列
    pendingVerifications.push({
      emailPrefix: targetEmail.split("@")[0],  // 用邮箱前缀匹配
      resolve,
      reject,
      timer,
    });
    
    // 立即轮询邮箱
    void pollMailbox();
  });
}
```

**IMAP 轮询逻辑**：

```typescript
async function pollMailbox(): Promise<void> {
  // 搜索未读邮件
  sharedImap.search(["UNSEEN"], (err, results) => {
    // 获取最近 10 封邮件
    const toFetch = results.slice(-Math.min(results.length, 10));
    const f = sharedImap!.fetch(toFetch, { bodies: "" });
    
    f.on("message", (msg) => {
      msg.on("body", (stream) => {
        stream.once("end", async () => {
          // 解析邮件内容
          const parsed = await simpleParser(buffer);
          const fullBody = (parsed.html || "") + "\n" + (parsed.text || "");
          
          // 提取激活链接中的 id 和 token
          // 匹配: /api/v1/auths/activate?id=xxx&token=yyy
          const match = fullBody.match(/\/api\/v1\/auths\/activate\?id=([a-f0-9-]+)&token=([a-f0-9]+)/);
          
          if (match) {
            const [, actId, actToken] = match;
            
            // 匹配邮箱前缀，确认是该账号的邮件
            for (const pv of pendingVerifications) {
              if (fullBody.includes(pv.emailPrefix)) {
                // 找到匹配，返回激活信息
                pv.resolve({ id: actId, token: actToken });
                break;
              }
            }
          }
        });
      });
    });
  });
}
```

**关键点**：
- 使用 **IMAP 协议** 自动读取邮箱
- 监听 **新邮件事件** (`sharedImap.on("mail")`)
- 每 **5 秒** 自动轮询一次
- 通过 **邮箱前缀** 匹配验证邮件
- 从邮件正文中 **正则提取** 激活链接

---

### 步骤 3: 激活账号 (activate)

```typescript
async function activate(id: string, token: string) {
  const resp = await fetch(
    `${QWEN_BASE_URL}/api/v1/auths/activate?id=${id}&token=${token}`, 
    {
      method: "GET",
      headers: { 
        Accept: "application/json", 
        source: "web", 
        version: "0.2.35" 
      },
      redirect: "manual",  // 不自动跟随重定向
    }
  );
  
  // 成功返回 302 或 200
  if (resp.status !== 302 && resp.status !== 200) {
    throw new Error(`Activation failed (${resp.status})`);
  }
}
```

**请求地址**: `GET https://chat.qwen.ai/api/v1/auths/activate?id=xxx&token=yyy`

---

### 步骤 4: 登录获取 JWT Token (signin)

```typescript
async function signin(email: string, password: string): Promise<string> {
  const passwordHash = sha256(password);
  
  const resp = await fetch(`${QWEN_BASE_URL}/api/v1/auths/signin`, {
    method: "POST",
    headers: { 
      "Content-Type": "application/json", 
      Accept: "application/json", 
      source: "web", 
      version: "0.2.35" 
    },
    body: JSON.stringify({ 
      email, 
      password: passwordHash 
    }),
  });
  
  return data.token;  // JWT Token
}
```

**请求地址**: `POST https://chat.qwen.ai/api/v1/auths/signin`

---

### 步骤 5: 获取 OAuth 凭证

```typescript
async function getOAuthCredentials(jwtToken: string) {
  // 1. 获取 Device Code
  const deviceResp = await fetch(`${QWEN_BASE_URL}/api/v1/oauth2/device/code`, {
    method: "POST",
    headers: { 
      Authorization: `Bearer ${jwtToken}`  // 使用 JWT Token
    },
    body: new URLSearchParams({
      client_id: QWEN_OAUTH_CLIENT_ID,
      scope: QWEN_OAUTH_SCOPE,
      code_challenge: codeChallenge,
      code_challenge_method: "S256",
    }),
  });
  
  // 2. 授权 Device Code
  const authResp = await fetch(`${QWEN_BASE_URL}/api/v2/oauth2/authorize`, {
    method: "POST",
    headers: { 
      Authorization: `Bearer ${jwtToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ 
      user_code: deviceData.user_code, 
      approve: true  // 自动同意
    }),
  });
  
  // 3. 换取 Access Token
  const tokenResp = await fetch(`${QWEN_BASE_URL}/api/v1/oauth2/token`, {
    method: "POST",
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:device_code",
      client_id: QWEN_OAUTH_CLIENT_ID,
      device_code: deviceData.device_code,
      code_verifier: codeVerifier,
    }),
  });
  
  return {
    access_token: tokenData.access_token,
    refresh_token: tokenData.refresh_token,
    token_type: tokenData.token_type,
    resource_url: tokenData.resource_url,
    expiry_date: Date.now() + tokenData.expires_in * 1000,
  };
}
```

---

## 三、主流程控制

```typescript
export function startAutoRegister(
  email: string,
  name: string,
  password: string,
  accountId: string,
): string {
  const id = crypto.randomUUID();
  
  // 创建流程记录
  inFlightRegistrations.set(id, {
    id, email, name, password, accountId,
    status: "registering",  // 初始状态
    startedAt: Date.now(),
  });
  
  // 异步执行注册流程
  void runAutoRegister(id);
  
  return id;  // 返回流程 ID，用于查询状态
}

async function runAutoRegister(id: string) {
  const flow = inFlightRegistrations.get(id);
  if (!flow) return;
  
  try {
    // 1. 注册
    await signup(flow.email, flow.name, flow.password);
    flow.status = "waiting_email";
    
    // 2. 等待验证邮件（IMAP 自动读取）
    const verifyInfo = await waitForVerificationEmail(flow.email, 180000);
    flow.status = "activating";
    
    // 3. 激活
    await activate(verifyInfo.id, verifyInfo.token);
    flow.status = "logging_in";
    
    // 4. 登录
    const jwtToken = await signin(flow.email, flow.password);
    flow.status = "getting_oauth";
    
    // 5. 获取 OAuth 凭证
    const oauthCreds = await getOAuthCredentials(jwtToken);
    
    // 完成
    flow.status = "success";
    flow.jwtToken = jwtToken;
    flow.oauthCredentials = oauthCreds;
    
  } catch (err: any) {
    flow.status = "error";
    flow.error = err.message;
  }
}
```

---

## 四、状态查询

```typescript
export function getAutoRegisterStatus(flowId: string) {
  const flow = inFlightRegistrations.get(flowId);
  if (!flow) return { status: "not_found" };
  
  return {
    status: flow.status,           // 当前状态
    error: flow.error,             // 错误信息
    accountId: flow.accountId,     // 账号 ID
    jwtToken: flow.jwtToken,       // JWT Token
    oauthCredentials: flow.oauthCredentials,  // OAuth 凭证
  };
}
```

**状态枚举**：
- `registering` - 正在注册
- `waiting_email` - 等待验证邮件
- `activating` - 正在激活
- `logging_in` - 正在登录
- `getting_oauth` - 正在获取 OAuth 凭证
- `success` - 注册成功
- `error` - 注册失败

---

## 五、API 使用方式

### 1. 启动自动注册
```bash
POST /admin/accounts/auto-register
{
  "email": "user@example.com",
  "name": "username",
  "password": "yourpassword",
  "accountId": "account1"
}
```

**返回**：
```json
{
  "flowId": "550e8400-e29b-41d4-a716-446655440000"
}
```

### 2. 轮询查询状态
```bash
POST /admin/accounts/auto-register-poll
{
  "flowId": "550e8400-e29b-41d4-a716-446655440000"
}
```

**返回**：
```json
{
  "status": "success",
  "accountId": "account1",
  "jwtToken": "eyJhbGciOiJIUzI1NiIs...",
  "oauthCredentials": {
    "access_token": "xxx",
    "refresh_token": "yyy",
    "expiry_date": 1234567890
  }
}
```

---

## 六、环境变量配置

```bash
# IMAP 邮箱配置（用于自动读取验证邮件）
IMAP_USER=your_email@qq.com
IMAP_PASSWORD=your_email_auth_code  # 邮箱授权码，不是登录密码
IMAP_HOST=imap.qq.com
IMAP_PORT=993

# 可选：代理配置（防止 IP 被封）
AUTO_REGISTER_PROXIES=http://proxy1:port,http://proxy2:port
```

---

这个自动注册系统非常完整，实现了：
1. ✅ 自动注册账号
2. ✅ **IMAP 自动读取验证邮件**
3. ✅ **自动提取激活链接**
4. ✅ 自动激活账号
5. ✅ 自动登录获取 JWT
6. ✅ 自动获取 OAuth 凭证
7. ✅ 保存到账号池

全程无需人工干预！