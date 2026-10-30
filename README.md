# 互站交易监控

每 10 分钟采集互站首页公开的最新交易，通过网页查看每日观察金额、商品、平台指数和采集状态。可在 Windows 本机运行，也可使用 Docker 部署到 Linux 服务器。

## 版本下载

请从 [GitHub Releases](https://github.com/70548887/huzhan-trade-monitor/releases) 下载对应版本的源码包；版本变更详见 [CHANGELOG.md](CHANGELOG.md)。源码包不包含运行数据、日志或 Python 虚拟环境。

## Linux 宝塔 / Python 项目部署

以下以 `/www/wwwroot/huzhan-trade-monitor` 为项目目录、`www` 为运行用户举例；请根据服务器实际情况修改。Python 建议使用 3.12。

1. 将发布版源码解压至项目目录，进入目录后创建虚拟环境并安装依赖：

   ```bash
   cd /www/wwwroot/huzhan-trade-monitor
   python3.12 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   mkdir -p data logs
   chown -R www:www data logs
   ```

   如果 Python 3.12 不在命令路径中，请将 `python3.12` 替换为宝塔 Python 环境中的实际解释器路径。

2. 在宝塔“Python 项目”添加项目，根目录选择项目目录，运行用户设为 `www`，使用自定义启动命令：

   ```bash
   /www/wwwroot/huzhan-trade-monitor/.venv/bin/python -m uvicorn huzhan_monitor.web:app --host 127.0.0.1 --port 8878
   ```

3. 在宝塔“计划任务”添加 Shell 任务，设置每 10 分钟执行一次，并使用与网页服务相同的运行用户。例如计划任务由 root 启动、网页使用 `www` 时：

   ```bash
   cd /www/wwwroot/huzhan-trade-monitor
   runuser -u www -- .venv/bin/python -m huzhan_monitor.collector
   ```

网页与采集任务需要对同一份 `data` 和 `logs` 目录拥有读写权限。网页服务不会自动运行采集器，计划任务必须单独配置。然后在宝塔配置反向代理，目标为 `http://127.0.0.1:8878`；开放公网前应配置访问验证或限制，并启用 HTTPS。

## Linux 宝塔 / Docker 部署

服务器需要安装 Git、Docker 和 Docker Compose。可以在宝塔终端中执行：

```bash
git clone https://github.com/70548887/huzhan-trade-monitor.git
cd huzhan-trade-monitor
docker compose up -d --build
docker compose ps
```

也可以在宝塔的 Docker 编排功能中使用仓库内的 `compose.yaml`。它会启动网页服务和采集服务两个容器；采集服务启动后立即采集一次，之后每 10 分钟采集。容器异常退出后会自动重启。

网页服务默认只绑定服务器的 `127.0.0.1:8878`。在宝塔中为域名配置反向代理，目标地址填写 `http://127.0.0.1:8878`，即可通过该站点访问。**页面当前没有登录功能，开放公网前应在反向代理层配置访问验证或限制访问来源，并启用 HTTPS。**

数据和日志保存在 Docker 命名卷中，重建容器不会清除。不要使用会删除卷的命令，除非确定不再需要这些数据。GitHub 不包含本机数据库；首次在服务器部署会建立新的采集基线。如果需要保留 Windows 上的历史数据，请先使用 SQLite 在线备份导出数据库，再迁移到服务器，不要直接复制正在运行的数据库文件。

常用命令：

```bash
docker compose logs -f collector    # 查看采集日志
docker compose logs -f web          # 查看网页服务日志
docker compose restart              # 重启服务
docker compose down                 # 停止服务，保留数据卷
```

## Windows 本机运行

- 打开本机页面：<http://127.0.0.1:8878/>，或双击“启动监控.bat”。
- 启用或重新安装定时任务：双击“安装定时任务.bat”。脚本会创建 Python 虚拟环境、安装依赖并启动页面。
- 停用定时任务：双击“停用定时任务.bat”。采集和登录自启动会被禁用，已有数据保留；重新启用时再运行安装脚本。
- 手动采集：在项目目录运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\collect.ps1`。

任务 `HuzhanMonitorCollector` 每 10 分钟执行；`HuzhanMonitorLogon` 在当前 Windows 用户登录时启动页面并采集一次。电脑关机、休眠或用户未登录时采集会中断。恢复后只采集当前公开列表，漏掉的条目无法补回。

页面只监听 `127.0.0.1:8878`。如果端口被占用，启动脚本会停止并提示，不会关闭占用端口的其他程序。

## 数据如何统计

- 金额以分存储。首次观察到“交易成功”的日期用于每日成功金额；首次观察到“正在交易”的日期用于每日新增进行中金额。
- “当日观察到的交易中金额”合计当天任意一次采集显示为正在交易的不同条目，包含启动时存量；同一条目可能后来成功，不能与成功金额相加。“最后一次采集的交易中金额”只合计所选日期最后一次成功采集的条目。交易明细可分别筛选并导出这两类条目。
- 第一次有效采集建立基线。已经成功的记录显示为“启动时存量”，不计入当日新增成功；基线中的进行中记录后来被观察到成功时才计入。
- 首页只返回有限条数，且没有订单编号或成交时间。指纹由商品链接（没有链接时用标题）、遮罩买家、头像和金额组成。相同指纹合并，所以同一买家对同款商品的同价交易可能被少计，列表滚动也可能造成漏采。
- “平台指数观测增量”是首页累计指数在两个实际快照之间的变化，页面显示起止时间。缺少午夜附近快照或指数回退会标记，不能当作完整自然日成交额。
- 每日没有成功采集时金额显示“—”。采集间隔超过 15 分钟、失败或备份异常时页面会提示。

## 文件、备份与检查

- 数据库：`data\monitor.sqlite3`。每日备份存于 `data\backups`，保留最近 30 份；原始响应保留 90 天，结构化记录长期保留。
- 日志：`logs\collector.log`、`logs\task-output.log` 和 `logs\server-error.log`。
- 首次采集后开始 24 小时试运行，页面在满 24 小时后显示成功次数、失败次数及最大间隔；如发生采集缺口或失败会标记。电脑需要持续运行。
- 验收测试：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`。

商品名、买家昵称和状态来自公开页面，页面结构变化、访问限制或空响应会记录为采集失败，不会解释为零交易。

