# Okeeper

A little alarm clock for your Codex sessions.

Okeeper 是运行在常开 Linux 服务器上的轻量网页：像设闹钟一样安排 Codex 消息，查看多个账号的额度，并按需自动切换账号。

- 每天或仅下一次发送；时间滚轮兼容手机触屏、鼠标滚轮和直接输入。
- 默认触发固定使用 **6 Luna Light**，发送 `ok`；每天复用一个触发会话。
- 可选择现有会话并填写提示词，沿用该会话的模型和思考强度。
- 查看多账号额度，手动切换并重启，或开启五小时额度用尽后的自动切换。
- 默认触发卡片可一键新增 **＋5小时01分** 的定时器。
- 本机 systemd 调度，统一台北时间；错过跳过、防止重复、记录结果。

## 运行

需要 Linux、用户级 systemd、Python 3.11+，以及已登录并运行的 Codex app-server。

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app/web_server.py
```

打开 `http://127.0.0.1:18793`。安装常驻服务与本机定时器：

```sh
.venv/bin/python scripts/install_services.py --start
```

如需通过自己的私有网络地址访问，使用 `--bind <服务器私网 IP>`。账号切换会重启主 Codex 后台，并中断它正在执行的任务。

## 测试

```sh
.venv/bin/python scripts/test.py
npm ci
OKEEPER_PYTHON="$PWD/.venv/bin/python" npm test
```

浏览器测试需要本机 Chrome/Chromium，可通过 `OKEEPER_BROWSER` 指定可执行文件。测试使用虚构账号和临时任务库；截图及报告写入不入库的 `artifacts/`。

## 项目文档

[交付文档](docs/HANDOVER.md) 说明核心设计、目录、配置、部署、迁移、执行证据与故障恢复。

网页使用本地保存的 daisyUI 和 Motion；上游信息及许可证见 [web/vendor/README.md](web/vendor/README.md)。
