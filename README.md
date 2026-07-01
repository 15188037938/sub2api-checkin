# Sub2API 每日签到服务

用户每日首次登录自动签到，赠送额度。后台可配赠送金额。

## 部署方式

### 方式一：一键部署（在 sub2api 服务器上）

```bash
# 1. 上传 signin_service.py 到服务器
scp server.py auto_topup.py root@你的服务器:/opt/checkin-service/

# 2. SSH 到服务器，运行部署脚本
ssh root@你的服务器
cd /opt/checkin-service
chmod +x deploy.sh

# 3. 设置环境变量并部署
export SUB2API_URL="http://localhost:8080"      # sub2api 地址
export SUB2API_ADMIN_KEY="sk-你的管理员APIKey"   # 管理后台 API Key
bash deploy.sh
```

### 方式二：手动启动（测试用）

```bash
cd /opt/checkin-service

# 不连接 sub2api（签到仅记录，额度手动发）
python3 server.py

# 连接 sub2api（签到自动生成兑换码）
SUB2API_URL="http://localhost:8080" \
SUB2API_ADMIN_KEY="sk-你的管理员Key" \
python3 server.py
```

### 方式三：Docker 部署

```bash
docker run -d --name checkin-service \
  --restart always \
  -p 18888:18888 \
  -v $(pwd)/checkin.db:/app/checkin.db \
  -e SUB2API_URL=http://localhost:8080 \
  -e SUB2API_ADMIN_KEY=sk-xxx \
  -e CHECKIN_PORT=18888 \
  -v $(pwd)/server.py:/app/server.py \
  python:3.11-slim python /app/server.py
```

## 嵌入 sub2api

部署后，在 sub2api 管理后台 **系统设置 -> 首页内容自定义**（或自定义菜单）中，添加签到页面：

- 菜单名称：每日签到
- 菜单链接：`http://IP:18888/`
- 打开方式：iframe 嵌入 或 新窗口

也可在管理员后台首页 HTML 中直接嵌入：
```html
<iframe src="http://你的服务器IP:18888/" style="width:100%;height:400px;border:none"></iframe>
```

## 配置管理

访问 `http://IP:18888/admin` 进行：
- 设置每日赠送额度（默认 0.5）
- 设置额度单位（USD/CNY/Token）
- 开关签到功能

## 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `SUB2API_URL` | sub2api 站点地址 | 自动发额度时需要 |
| `SUB2API_ADMIN_KEY` | 管理员 API Key | 自动发额度时需要 |
| `CHECKIN_PORT` | 签到服务端口 | 默认 18888 |

## 自动发放额度

### 方案 A：签到直接生成兑换码（推荐）

配置 `SUB2API_URL` 和 `SUB2API_ADMIN_KEY`，每次签到自动生成兑换码，用户可直接使用。

### 方案 B：定时脚本批量发放

```bash
# 每5分钟运行一次，自动为未处理的签到创建兑换码
*/5 * * * * python3 /opt/checkin-service/auto_topup.py \
  --sub2api-url http://localhost:8080 \
  --admin-key sk-xxx \
  --checkin-db /opt/checkin-service/checkin.db
```

### 方案 C：手动发放

不配置 SUB2API_* 环境变量，在管理后台 `http://IP:18888/admin` 查看每日签到统计，手动批量发放。

## 工作原理

```
用户访问签到页 --> 提交 user_id --> 检查今日是否已签到
  |                                      |
  | 未签到                           已签到
  v                                      v
记录签到 + 尝试发放额度           显示"今日已签到"
  |
  v
SUB2API_URL已配置? 
  |               |
  是              否
  v               v
调用sub2api    仅记录DB
创建兑换码     (后续手动发放)
```
