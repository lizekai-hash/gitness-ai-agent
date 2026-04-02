# Harness
Harness Open Source is an open source development platform packed with the power of code hosting, automated DevOps pipelines, hosted development environments (Gitspaces), and artifact registries.

## Overview
Harness Open source is an open source development platform packed with the power of code hosting, automated DevOps pipelines, Gitspaces, and artifact registries.


## 快速启动 (Quick Start)

本节介绍从零克隆项目到完整运行 **Harness 后端 + AI Agent 守护进程** 的全流程，提供两种方式。

---

## 方法一：Docker 一键启动（推荐）

只需安装 [Docker](https://docs.docker.com/get-docker/)，执行三步即可完成启动。

### 步骤 1：克隆仓库

```bash
git clone https://github.com/harness/harness.git
cd harness
```

### 步骤 2：配置环境变量

在项目根目录创建 `.env` 文件：

```bash
# .env
HARNESS_TOKEN=         # 启动后从 Harness UI 或 CLI 获取，首次留空
LLM_API_KEY=<你的 API Key>
LLM_BASE_URL=https://api.deepseek.com   # 或其他 OpenAI 兼容端点
LLM_MODEL=deepseek-chat                  # 或 claude-sonnet-4-6 等
HARNESS_SPACE=test
```

> `HARNESS_TOKEN` 首次启动时可留空，待 Harness 启动后再通过 UI 生成 PAT 并填入，然后重启 agent 容器。

### 步骤 3：启动所有服务

```bash
docker compose up -d
```

所有服务启动后：

| 服务 | 地址 |
|------|------|
| Harness UI | http://localhost:3000 |
| Agent Dashboard | http://localhost:3001 |
| WebSocket | ws://localhost:3002 |

**首次登录并生成 Token：**

```bash
# 登录 Harness（默认账号 admin@gitness.io / changeit）
docker exec -it harness /app/gitness login

# 生成 PAT
docker exec -it harness /app/gitness user pat "agent-pat" 2592000
```

将输出的 Token 填入 `.env` 的 `HARNESS_TOKEN`，然后重启 agent：

```bash
docker compose restart agent
```

**常用命令：**

```bash
docker compose logs -f agent    # 查看 Agent 日志
docker compose logs -f harness  # 查看 Harness 日志
docker compose down             # 停止所有服务
docker compose down -v          # 停止并删除数据卷（清空数据）
```

---

## 方法二：本地手动启动

### 1. 前置依赖

| 依赖 | 版本要求 | 说明 |
|------|----------|------|
| [Go](https://go.dev/dl/) | 1.20+ | 编译 Harness 后端 |
| [Node.js](https://nodejs.org/) + [Yarn](https://yarnpkg.com/) | Node 18+ | 构建前端（可选，二进制已内嵌） |
| [Python](https://www.python.org/downloads/) | 3.10+ | 运行 AI Agent 守护进程 |
| Git | 任意版本 | 克隆仓库 |

### 2. 克隆仓库

```bash
git clone https://github.com/harness/harness.git
cd harness
```

### 3. 构建 Harness 后端

若仓库中已有预编译的 `gitness.exe`（Windows）或 `gitness`（Linux/macOS），可跳过此步骤，直接进入第 4 步。

```bash
# 安装 Go 工具依赖
make dep
make tools

# （可选）构建前端
cd web && yarn install && yarn build && cd ..

# 编译后端二进制
make build
# 产物：./gitness（Linux/macOS）或 ./gitness.exe（Windows）
```

### 4. 配置 Harness 环境变量

项目根目录已有 `.local.env` 配置文件，包含本地开发所需的默认值。关键配置如下：

```bash
# .local.env（无需修改即可使用默认值启动）
GITNESS_PRINCIPAL_ADMIN_EMAIL=admin@gitness.io
GITNESS_PRINCIPAL_ADMIN_PASSWORD=changeit
GITNESS_HTTP_HOST=localhost
GITNESS_SSH_ENABLE=true
GITNESS_SSH_PORT=2222
```

### 5. 启动 Harness 后端

```bash
# Windows
./gitness.exe server .local.env

# Linux / macOS
./gitness server .local.env
```

服务启动后访问 http://localhost:3000，使用 `admin@gitness.io` / `changeit` 登录。

### 6. 获取 Harness PAT（Personal Access Token）

Agent 需要 Token 才能在 Harness 中自动创建仓库。在 Harness 启动后执行：

```bash
# Windows
./gitness.exe login
./gitness.exe user pat "agent-pat" 2592000

# Linux / macOS
./gitness login
./gitness user pat "agent-pat" 2592000
```

复制命令输出的 Token 字符串，用于下一步配置。

### 7. 安装 Agent Python 依赖

```bash
cd agent
pip install -r requirements.txt
```

### 8. 配置 Agent 环境变量

在 `agent/` 目录下创建 `.env` 文件，或直接在 shell 中导出以下变量：

```bash
# Harness 连接配置
export HARNESS_BASE_URL=http://localhost:3000   # Harness 服务地址
export HARNESS_TOKEN=<第 6 步获取的 PAT>        # 必填
export HARNESS_SPACE=test                        # 默认空间名

# LLM 配置（支持 DeepSeek / Claude / 任何 OpenAI 兼容接口）
export LLM_API_KEY=<你的 API Key>
export LLM_BASE_URL=https://api.deepseek.com    # 或其他兼容端点
export LLM_MODEL=deepseek-chat                   # 或 claude-sonnet-4-6 等
```

> Windows PowerShell 使用 `$env:HARNESS_TOKEN = "..."` 语法；Windows CMD 使用 `set HARNESS_TOKEN=...`。

### 9. 启动 Agent 守护进程

```bash
# 在 agent/ 目录下
python daemon.py
```

启动成功后终端显示：

```
============================================================
  AI Agent 自动化开发系统已启动
  Dashboard  : http://localhost:3001
  WebSocket  : ws://localhost:3002
  Harness    : http://localhost:3000
============================================================
```

### 10. 验证启动

| 服务 | 地址 | 说明 |
|------|------|------|
| Harness UI | http://localhost:3000 | 代码托管、仓库管理 |
| Agent Dashboard | http://localhost:3001 | Pipeline 触发与进度监控 |
| WebSocket | ws://localhost:3002 | 实时事件推送 |

在 Agent Dashboard 中选择仓库（需在 Harness 中预先创建，描述填写需求），点击 **Trigger** 即可启动自动开发流程。

### 11. 可选：命令行直接触发

```bash
cd agent

# 交互式输入需求
python run.py

# 直接传入需求（自动建仓库）
python run.py "写一个贪吃蛇 Python 游戏"

# 指定仓库名
python run.py -n my-snake "写一个贪吃蛇 Python 游戏"

# 仅本地生成，不推送到 Harness
python run.py --no-repo "写一个贪吃蛇 Python 游戏"
```

---

## Running Harness locally
> The latest publicly released docker image can be found on [harness/harness](https://hub.docker.com/r/harness/harness).

To install Harness yourself, simply run the command below. Once the container is up, you can visit http://localhost:3000 in your browser.

```bash
docker run -d \
  -p 3000:3000 \
  -p 3022:3022 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /tmp/harness:/data \
  --name harness \
  --restart always \
  harness/harness
```
> The Harness image uses a volume to store the database and repositories. It is highly recommended to use a bind mount or named volume as otherwise all data will be lost once the container is stopped.

See [developer.harness.io](https://developer.harness.io/docs/open-source) to learn how to get the most out of Harness.

## Where is Drone?

Harness Open Source represents a massive investment in the next generation of Drone. Where Drone focused solely on continuous integration, Harness adds source code hosting, developer environments (gitspaces), and artifact registries; providing teams with an end-to-end, open source DevOps platform.

The goal is for Harness to eventually be at full parity with Drone in terms of pipeline capabilities, allowing users to seamlessly migrate from Drone to Harness.

But, we expect this to take some time, which is why we took a snapshot of Drone as a feature branch [drone](https://github.com/harness/harness/tree/drone) ([README](https://github.com/harness/harness/blob/drone/.github/readme.md)) so it can continue development.

As for Harness, the development is taking place on the [main](https://github.com/harness/harness/tree/main) branch.

For more information on Harness, please visit [developer.harness.io](https://developer.harness.io/).

For more information on Drone, please visit [drone.io](https://www.drone.io/).

## Harness Open Source Development
### Pre-Requisites

Install the latest stable version of Node and Go version 1.20 or higher, and then install the below Go programs. Ensure the GOPATH [bin directory](https://go.dev/doc/gopath_code#GOPATH) is added to your PATH.

Install protobuf
- Check if you've already installed protobuf ```protoc --version```
- If your version is different than v3.21.11, run ```brew unlink protobuf```
- Get v3.21.11 ```curl -s https://raw.githubusercontent.com/Homebrew/homebrew-core/9de8de7a533609ebfded833480c1f7c05a3448cb/Formula/protobuf.rb > /tmp/protobuf.rb```
- Install it ```brew install /tmp/protobuf.rb```
- Check out your version ```protoc --version```

Install protoc-gen-go and protoc-gen-go-rpc:

- Install protoc-gen-go v1.28.1 ```go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.28.1```
(Note that this will install a binary in $GOBIN so make sure $GOBIN is in your $PATH)

- Install protoc-gen-go-grpc v1.2.0 ```go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.2.0```

```bash
$ make dep
$ make tools
```

### Build

First step is to build the user interface artifacts:

```bash
$ pushd web
$ yarn install
$ yarn build
$ popd
```

After that, you can build the Harness binary:

```bash
$ make build
```

### Run

This project supports all operating systems and architectures supported by Go.  This means you can build and run the system on your machine; docker containers are not required for local development and testing.

To start the server at `localhost:3000`, simply run the following command:

```bash
./gitness server .local.env
```

### Docker Configuration for Pipelines

Harness pipelines run inside Docker containers. The application automatically negotiates the Docker API version with your Docker daemon, so it works with various Docker versions including Docker Desktop, Rancher Desktop, Colima, and native Docker on Linux.

**Docker Socket Location**

By default, Harness expects the Docker socket at `/var/run/docker.sock`. If you're using an alternative Docker runtime, you may need to configure the socket location:

| Runtime | Socket Location | Configuration |
|---------|-----------------|---------------|
| Docker Desktop | `/var/run/docker.sock` | Works by default |
| Rancher Desktop | `~/.rd/docker.sock` | Create symlink or set `GITNESS_DOCKER_HOST` |
| Colima | `~/.colima/default/docker.sock` | Create symlink or set `GITNESS_DOCKER_HOST` |
| Linux (native) | `/var/run/docker.sock` | Works by default |

**Option 1: Create a symlink (recommended)**
```bash
# For Rancher Desktop
sudo ln -sf ~/.rd/docker.sock /var/run/docker.sock

# For Colima
sudo ln -sf ~/.colima/default/docker.sock /var/run/docker.sock
```

**Option 2: Set environment variable**

Add to your `.local.env`:
```bash
# For Rancher Desktop
GITNESS_DOCKER_HOST=unix:///Users/<username>/.rd/docker.sock

# For Colima
GITNESS_DOCKER_HOST=unix:///Users/<username>/.colima/default/docker.sock
```

**Pinning Docker API Version**

The application automatically negotiates the API version with your Docker daemon. If you need to pin a specific version (e.g., for compatibility testing), you can set:
```bash
GITNESS_DOCKER_API_VERSION=1.45
```

### Auto-Generate Harness API Client used by UI using Swagger
Please make sure to update the autogenerated client code used by the UI when adding new rest APIs.

To regenerate the code, please execute the following steps:
- Regenerate swagger with latest Harness binary `./gitness swagger > web/src/services/code/swagger.yaml`
- navigate to the `web` folder and run `yarn services`

The latest API changes should now be reflected in `web/src/services/code/index.tsx`

# Run Registry Conformance Tests
```
make conformance-test
```
For running conformance tests with existing running service, use:
```
make hot-conformance-test
```

## User Interface

This project includes a full user interface for interacting with the system. When you run the application, you can access the user interface by navigating to `http://localhost:3000` in your browser.

## REST API

This project includes a swagger specification. When you run the application, you can access the swagger specification by navigating to `http://localhost:3000/swagger` in your browser (for raw yaml see `http://localhost:3000/openapi.yaml`).
For registry endpoints, currently swagger is located on different endpoint `http://localhost:3000/registry/swagger/` (for raw json see `http://localhost:3000/registry/swagger.json`). These will be later moved to the main swagger endpoint. 


For testing, it's simplest to just use the cli to create a token (this requires Harness server to run):
```bash
# LOGIN (user: admin, pw: changeit)
$ ./gitness login

# GENERATE PAT (1 YEAR VALIDITY)
$ ./gitness user pat "my-pat-uid" 2592000
```

The command outputs a valid PAT that has been granted full access as the user.
The token can then be send as part of the `Authorization` header with Postman or curl:

```bash
$ curl http://localhost:3000/api/v1/user \
-H "Authorization: Bearer $TOKEN"
```


## CLI
This project includes VERY basic command line tools for development and running the service. Please remember that you must start the server before you can execute commands.

For a full list of supported operations, please see
```bash
$ ./gitness --help
```

## Contributing

Refer to [CONTRIBUTING.md](https://github.com/harness/harness/blob/main/CONTRIBUTING.md)

## License

Apache License 2.0, see [LICENSE](https://github.com/harness/harness/blob/main/LICENSE).
