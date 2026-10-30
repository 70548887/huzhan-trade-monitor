# 部署指南

[返回项目首页](../README.md) · [常见问题](troubleshooting.md)

本项目由网页服务和采集服务组成。宝塔原生部署需要分别启动它们；Docker Compose 会管理两个服务；Windows 安装脚本会创建相应的计划任务。

本指南的宝塔原生示例使用 `38761` 端口。现有 Docker 和 Windows 配置默认使用 `8878`。端口以实际启动配置为准，反向代理必须指向相同端口。

<a id="account"></a>

## 首次登录与管理员账号

从 v0.2.0 起默认启用登录保护。没有默认密码，也没有公开注册入口；未初始化账号时，首页和数据接口保持锁定。账号存于现有 SQLite 数据库，不会覆盖交易记录。

**宝塔最简配置**：在项目的“环境变量”中指定 `HUZHAN_ADMIN_USERNAME=admin`，将 `HUZHAN_ADMIN_PASSWORD` 设为自己选择的密码（至少 12 个字符），然后重启项目。首次创建成功后可以移除密码环境变量；已有管理员时，这两个变量不会覆盖账号。

**本机命令方式**：在项目根目录，使用实际项目环境的 Python 执行以下命令，按提示输入两次密码：

```bash
python -m huzhan_monitor.admin --username admin
```

这里的 `python` 代表项目实际解释器。宝塔环境中使用其完整路径，并以与网页相同的用户执行；Windows 使用 `.venv\Scripts\python.exe`；Docker 已启动时可用 `docker compose exec web python -m huzhan_monitor.admin --username admin`。

忘记密码时，在上述命令末尾加 `--reset`。重置会撤销所有登录会话。用户名支持 3–64 位英文字母、数字及 `_ . @ -`，密码支持 12–1024 个字符；账号不能在网页中注册或重置。

网页会话最长 12 小时。相同来源在 15 分钟内连续尝试达到限制时，需要等待窗口结束再重试。`/health` 只返回服务状态，不包含交易数据，因此仍允许未登录访问。

### HTTPS 反向代理

公网登录需使用 HTTPS，并设置 `HUZHAN_COOKIE_SECURE=1`。本地纯 HTTP 调试保持为 `0`，否则浏览器不会发送登录 Cookie。代理应保留客户端的 Host（Nginx 例如 `proxy_set_header Host $http_host;`）；不要把 Host 替换成后端的 `127.0.0.1`，否则登录表单校验可能失败。

<a id="baota"></a>

## 宝塔 Python 项目

### 1. 放置源码

从 [Releases](https://github.com/70548887/huzhan-trade-monitor/releases/latest) 下载源码包并解压。以下以 `/www/wwwroot/huzhan-trade-monitor` 为项目根目录。

确认根目录下直接包含这两个路径：

```text
/www/wwwroot/huzhan-trade-monitor/requirements.txt
/www/wwwroot/huzhan-trade-monitor/huzhan_monitor/web.py
```

如果解压后多了一层版本目录，应选择那一层作为项目根目录，并同步调整下面所有路径。不要将 Python 包目录 `huzhan_monitor` 本身选为项目根目录。

### 2. 在宝塔中添加项目

使用宝塔管理的 Python 环境，无需为照抄示例再创建一个 `.venv`。

- **项目名称**：`huzhan-trade-monitor`。
- **Python 环境**：选择已安装的 Python 3.12 环境。
- **启动方式**：命令行启动。
- **项目路径**：`/www/wwwroot/huzhan-trade-monitor`。
- **启动用户**：`www`；如使用其他用户，后续目录权限和采集任务也应使用该用户。
- **安装依赖包**：选择项目根目录的 `requirements.txt`，或在该项目环境中安装此文件中的依赖。
- **环境变量**：按[账号配置](#account)初始化管理员；自定义存储位置见[数据目录](#data)。
- **开机启动**：按需启用，长期监控时建议启用。

启动命令：

```bash
uvicorn huzhan_monitor.web:app --app-dir /www/wwwroot/huzhan-trade-monitor --host 127.0.0.1 --port 38761 --workers 1
```

该命令使用所选环境里的 `uvicorn`。如果面板提示找不到这个命令，在环境管理中复制该环境实际的 Python 解释器路径，使用 `完整Python路径 -m uvicorn` 替换开头的 `uvicorn`，其余参数不变。不要填写尚未创建的 `.venv/bin/python`，也不要假设服务器终端里的 `python` 与宝塔项目环境相同。

首次启动前，确保 `www` 可以写入数据和日志目录。以下命令在宝塔服务器终端以管理员身份执行：

```bash
cd /www/wwwroot/huzhan-trade-monitor || exit 1
mkdir -p data logs
chown -R www:www data logs
```

### 3. 启动网页并配置域名

保存配置并启动项目。在同一台服务器终端确认服务响应：

```bash
curl --fail --silent --show-error http://127.0.0.1:38761/health
```

正常返回 `huzhan-monitor-ok`。随后在宝塔为站点设置反向代理，目标地址为：

```text
http://127.0.0.1:38761
```

`127.0.0.1` 只供服务器本机访问；其他电脑或手机通过配置好的站点域名访问。首次访问会显示登录页，请完成[管理员初始化和 HTTPS 配置](#account)。

### 4. 配置每 10 分钟采集

网页运行不代表采集已经开始。在宝塔“计划任务”中添加 Shell 任务，执行周期设置为每 10 分钟。

先在宝塔环境管理中复制**与网页相同环境**的 Python 解释器完整路径，替换下面 `HZ_PYTHON` 的值。此示例适用于计划任务以 root 执行、项目以 `www` 运行的情况：

```bash
# 必须替换为宝塔项目环境中实际存在的 Python 路径。
HZ_PYTHON='/替换为实际环境路径/bin/python'
cd /www/wwwroot/huzhan-trade-monitor || exit 1
runuser -u www -- "$HZ_PYTHON" -m huzhan_monitor.collector
```

如果计划任务已经以 `www` 执行，最后一行直接使用 `"$HZ_PYTHON" -m huzhan_monitor.collector`。任务与网页必须共用相同的数据目录和读写权限。

保存后手动执行一次，查看任务日志中的“采集成功”或具体失败原因，再刷新网页查看最近采集时间。已有独立采集进程时，不要再重复配置另一种调度方式。

参考：[宝塔 Python 项目管理](https://docs.bt.cn/ai-ops/mcp/tools-reference/)、[计划任务](https://docs.bt.cn/user-guide/crontab/task/)、[反向代理](https://docs.bt.cn/user-guide/site/php/site-config/reverse-proxy)。

<a id="docker"></a>

## Docker Compose

### 启动

服务器需安装 Git、Docker 和 Docker Compose。在固定的部署目录运行：

```bash
git clone https://github.com/70548887/huzhan-trade-monitor.git
cd huzhan-trade-monitor
docker compose up -d --build
docker compose ps
```

首次启动后可执行 `docker compose exec web python -m huzhan_monitor.admin --username admin` 创建账号。也可在启动前把 `.env.example` 复制为 `.env`，填写自己的初始密码。`.env` 不会提交到 GitHub；不要使用示例或公开密码。已创建账号后，移除初始密码变量不会影响账号。

`git clone` 获取 `main` 分支最新代码；如需固定版本，可下载对应 Release 的源码包，在解压后的项目根目录运行 Compose 命令。保持部署目录和 Compose 项目名不变，避免更新时创建新的数据卷。

[`compose.yaml`](../compose.yaml) 定义了两个服务：

- `web`：网页服务，通过服务器本地的 `127.0.0.1:8878` 提供查询。
- `collector`：首次启动立即采集，之后每 10 分钟执行；不需要另建宝塔采集计划任务。

两个服务共用 `monitor_data` 和 `monitor_logs` 命名卷，容器重建后数据仍保留。此方式需要在目标服务器构建镜像；本仓库未提供预构建镜像。

### 端口与访问

默认反向代理目标为 `http://127.0.0.1:8878`。如果宿主机端口被占用，可以将 Compose 的 `web.ports` 改为：

```yaml
ports:
  - "127.0.0.1:38761:8878"
```

这里改变的是宿主机端口，容器内端口仍是 `8878`，健康检查不需要改。重新运行 `docker compose up -d` 后，将反向代理目标同步改为 `http://127.0.0.1:38761`。

### 日常管理

```bash
docker compose logs --tail=50 collector
docker compose logs --tail=50 web
docker compose restart
docker compose down
```

最后一条命令停止并移除容器，但保留命名卷。`docker compose down -v` 会删除数据卷，不适用于保留数据的普通停机或更新。

<a id="windows"></a>

## Windows 本机

1. 安装 Python 3.12，确保终端可以执行 `python`。
2. 将源码解压到固定目录，双击 `安装定时任务.bat`。脚本会创建 `.venv`、安装依赖、注册任务并启动网页服务。
3. 在项目目录执行 `.\.venv\Scripts\python.exe -m huzhan_monitor.admin --username admin`，按提示创建账号。
4. 双击 `启动监控.bat`，或打开 `http://127.0.0.1:8878/`，使用刚创建的账号登录。

这里的 `.venv` 由 Windows 安装脚本创建，与宝塔管理的环境是两种部署方式。

- `HuzhanMonitorCollector`：每 10 分钟执行一次采集。
- `HuzhanMonitorLogon`：当前用户登录后启动网页服务并补采一次。
- `停用定时任务.bat`：停用上述任务，保留历史数据；它不会自动关闭已经启动的网页进程。

电脑需要保持开机、联网并登录 Windows。关机、休眠或未登录期间无法持续采集。需要手动采集时，在项目目录执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\collect.ps1
```

Windows 脚本默认使用 `8878`，没有提供端口环境变量。发生端口冲突时，先按[排错指南](troubleshooting.md#port)识别占用程序。

<a id="data"></a>

## 数据、备份与更新

### 存储位置

- SQLite 数据库：`data/monitor.sqlite3`。
- 每日备份：`data/backups/`，保留最近 30 份。
- 原始响应：压缩存储于数据库，保留 90 天。
- 采集日志：`logs/collector.log`。
- Windows 任务与网页日志：`logs/task-output.log`、`logs/server-out.log`、`logs/server-error.log`。

原生部署可使用 `HUZHAN_MONITOR_DATA_DIR` 和 `HUZHAN_MONITOR_LOG_DIR` 环境变量更改目录。网页服务和采集任务必须设置为同一路径。Docker 默认使用挂载命名卷的 `/app/data` 和 `/app/logs`。

数据库和备份也包含管理员密码哈希及会话记录，需要妥善保管。账号密码不写入采集日志；不要公开上传数据库、备份或本地生成的凭据文件。

### 备份与迁移

GitHub 和发布包不包含运行数据库。新机器首次部署会建立新的采集基线。

迁移现有数据时，先停止网页和采集进程，再备份完整数据目录；运行中备份应使用 SQLite 在线备份机制。数据库可能正在使用 WAL 日志，直接复制运行中的单个 `monitor.sqlite3` 文件可能遗漏尚未写回的数据。

### 更新程序

更新前备份数据，并保留现有的运行环境、目录权限、启动命令和采集配置。使用 Git 安装且没有本地修改时，可以执行 `git pull --ff-only`；本地有修改时，先检查并保留，不要强制覆盖。

- **宝塔**：在项目使用的 Python 环境中更新 `requirements.txt` 依赖，再重启网页服务。采集计划任务继续使用相同解释器和数据目录。
- **Docker**：保持同一 Compose 项目名，在原部署目录执行 `docker compose up -d --build`；不要删除数据卷。
- **Windows**：更新源码后，在现有 `.venv` 中安装 `requirements.txt` 依赖，重启网页进程；修改部署目录时需要重新注册计划任务。

已有 Release 源码包及标签不会随 `main` 文档更新而变化。[版本记录](../CHANGELOG.md)记录每次正式发布内容。
