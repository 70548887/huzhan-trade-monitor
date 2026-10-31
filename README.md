# 互站交易监控

[![Release](https://img.shields.io/github/v/release/70548887/huzhan-trade-monitor?style=flat-square&color=ea7b3c)](https://github.com/70548887/huzhan-trade-monitor/releases/latest)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/Web-FastAPI-009688?style=flat-square)
![SQLite](https://img.shields.io/badge/Storage-SQLite-003B57?style=flat-square)

自动记录互站首页公开的交易动态，按日期查看成功交易、进行中金额和商品明细。默认每 10 分钟采集，可在设置中调整。

[下载版本](https://github.com/70548887/huzhan-trade-monitor/releases/latest) · [宝塔部署](docs/deployment.md#baota) · [Docker 部署](docs/deployment.md#docker) · [Windows 使用](docs/deployment.md#windows) · [常见问题](docs/troubleshooting.md) · [版本记录](CHANGELOG.md)

> **统计范围**：本项目记录的是采集到的公开展示条目。首页列表有限，且不提供订单编号和成交时间，因此数据可能漏采或合并相同交易，不能代表全站完整成交额。

## 功能

- **自动采集**：启动网页项目即在后台采集，此后每 10 分钟执行，保存原始响应、观察时间和状态变化。
- **频率设置**：登录后在“设置”中选择每小时等常用频率，或自定义 1–1440 分钟，保存后无需重启。
- **每日概览**：查看新增成功金额、当日观察到的交易中金额及最后一次采集的交易中金额。
- **商品明细**：按日期、统计类别和商品关键词筛选，支持 CSV 导出。
- **历史趋势**：展示观察到的每日成功金额，并单独记录平台累计指数的变化。
- **运行记录**：显示采集失败、时间缺口、备份异常和 24 小时试运行结果。
- **登录保护**：交易概览与运行日志通过管理员登录访问，数据接口和 CSV 导出同样受保护。
- **本地存储**：使用 SQLite，无需单独安装 MySQL；采集记录和备份保存在部署机器上。

## 开始使用

选择一种部署方式即可。从 v0.3.0 起，网页服务内置自动采集，默认不需要独立采集脚本或计划任务。依赖仍需首次安装。

### 宝塔 Python 项目

适合已经安装 Python 环境的 Linux 宝塔服务器，无需 Docker。

1. 从 [Releases](https://github.com/70548887/huzhan-trade-monitor/releases/latest) 下载源码包，解压到项目目录。
2. 使用宝塔管理的 Python 3.12 环境安装 `requirements.txt`，以命令行方式启动网页。
3. 配置管理员与域名反向代理，启动项目即可自动采集；升级时停用旧的采集计划任务。

从 v0.2.0 起，首次启动还需要[创建管理员账号](docs/deployment.md#account)。未配置账号时，页面保持锁定，不会公开展示数据。

[查看完整填写示例 →](docs/deployment.md#baota)

### Docker Compose

适合希望通过一个容器运行网页与自动采集的 Linux 服务器。

```bash
git clone https://github.com/70548887/huzhan-trade-monitor.git
cd huzhan-trade-monitor
docker compose up -d --build
```

Compose 会启动包含网页与自动采集的 `web` 服务，数据通过命名卷持久化。默认网页地址为部署机器上的 `http://127.0.0.1:8878`。旧版本升级需加 `--remove-orphans` 移除原来的独立 collector 容器。

[查看端口、数据卷与更新说明 →](docs/deployment.md#docker)

### Windows 本机

安装 Python 3.12 后，在解压目录中双击 `安装自启动.bat`（旧的 `安装定时任务.bat` 仍兼容）。完成管理员配置后，双击 `启动监控.bat`，或打开 [本机监控页面](http://127.0.0.1:8878/)。服务运行期间会自动采集。

[查看首次安装和停用方式 →](docs/deployment.md#windows)

## 统计口径

所有日期按北京时间（UTC+8）划分，金额以整数“分”存储。以下指标含义不同，不能直接相加。

- **新增成功条目金额**：当天首次观察到“交易成功”的条目金额。首次启动时已经成功的条目作为存量，不计入新增。
- **当日观察到的交易中金额**：当天任一轮采集显示为“正在交易”的不同条目金额，包含启动时存量。同一条目当天重复出现只计一次，它之后可能交易成功。
- **最后一次采集的交易中金额**：所选日期最后一次成功采集时，列表中正在交易的不同条目金额。条目从列表消失并不表示它已经成交。
- **新增进行中条目金额**：首次观察到“正在交易”的日期为当天、且不属于首次启动基线的条目金额。
- **平台指数观测增量**：首页累计金额或笔数在两个实际快照之间的变化，页面标注起止时间；这项数据独立于采集明细。

去重使用商品链接（无有效链接时使用标题）、遮罩买家昵称、头像标识和金额。同一买家再次购买同款、同价商品可能被合并。无成功采集的日期显示“—”，而不是零交易。

## 数据与运行

- 数据库默认位于 `data/monitor.sqlite3`，结构化历史长期保留。
- 采集成功后的维护会每天生成一份数据库备份，保留最近 30 份；原始响应保留 90 天。
- 采集日志默认位于 `logs/collector.log`。Docker 使用命名卷中的 `/app/data` 和 `/app/logs`。
- 超过当时设定的采集间隔及 5 分钟宽限期会显示缺口。恢复后只能获取当前列表，无法补回已经滚出列表的交易。
- 网页、日志、接口和导出统一要求管理员登录。公网部署需启用 HTTPS，并按部署指南设置安全 Cookie。

[查看数据目录、更新与备份说明 →](docs/deployment.md#data)

## 版本与部署状态

[Releases](https://github.com/70548887/huzhan-trade-monitor/releases/latest) 提供版本标签、变更说明、ZIP / TAR.GZ 源码包和 SHA256 校验文件。源码包不包含采集数据、日志、虚拟环境、桌面可执行程序或预构建的 Docker 镜像。

本项目已有 Windows 运行记录；Linux 文件锁和 Docker Compose 配置已提供，Docker 构建及 Linux 宝塔完整部署仍需在目标服务器确认。部署请以仓库中的最新在线指南为准，已发布的源码包保留发布时的文档。

## 文档与反馈

- [部署指南](docs/deployment.md)：宝塔 Python 项目、Docker Compose、Windows 和数据管理。
- [常见问题](docs/troubleshooting.md)：启动命令、模块路径、端口占用、采集与统计问题。
- [版本记录](CHANGELOG.md)：每个版本的功能和已知限制。
- [提交问题](https://github.com/70548887/huzhan-trade-monitor/issues)：请提供部署方式、Python 版本、启动命令和最新错误末尾；分享日志前移除密码、令牌等敏感内容。
