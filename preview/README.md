# Okeeper 开源版预览

此目录是开源产品化方案的交互演示，使用虚构账号与模拟回执。它没有连接现有 Okeeper 后台，不会发送真实消息、切换账号或创建后台定时任务。刷新页面会恢复示例数据。

预览包含首次设置、本地与云端工作台、定时编辑、睡眠补发和结果核查。代码已从聊天原型导出，移除聊天宿主依赖；HTML、CSS 和 JavaScript 均由本地提供，无外部字体、脚本或 API 请求。

## 本地预览

在仓库根目录执行：

```sh
python3 -m http.server 18794 --bind 127.0.0.1 --directory preview/public
```

打开 `http://127.0.0.1:18794`。通过 Tailscale 分享时，将绑定地址替换为执行设备的 Tailscale IPv4 地址；只对 `preview/public` 提供静态文件。

## 常驻部署

仓库的 `deploy/systemd/okeeper-preview.service.in` 是独立预览服务模板。将 `@ROOT@`、`@PYTHON@`、`@TAILSCALE_IP@` 替换为本机值，保存到用户级 systemd 目录中的 `okeeper-preview.service` 后启用。

这项服务只提供预览页面，不启动已有调度器、额度监测器或账号执行器。停止预览服务不会影响正式控制台。

产品与后续实施范围见 [开源产品化方案](../docs/OPEN_SOURCE_PROPOSAL.md)。Windows/macOS 安装包、真实授权和独立云端执行尚未交付。
