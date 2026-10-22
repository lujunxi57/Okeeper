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

## 与 main 对齐的视觉设计

预览沿用 main 的浅绿底色、1080px 页面容器、紧凑顶栏、“赛博早八”标题、统计与时钟双卡、账号额度和触发队列、时间卡片及吉祥物。首次设置、运行记录和运行位置入口保留在轻量导航中。桌面宽屏默认展开吉祥物，较窄屏幕默认使用可展开的圆形入口。

`public/main-style.css` 是 2026-10-23 从正在使用的 main 工作区 `web/style.css` 同步的基础样式；`public/style.css` 只负责预览独有的组件与适配。`public/icon.png` 复用原有品牌图标，吉祥物结构复用 main 的内联资源。这里仅同步界面，不引入真实账号数据或主服务操作。

标题配色、时钟与吉祥物动效保留 main 的风格；减少动态效果设置仍有效。手机端保持单列布局，导航、表单和触发开关可用键盘操作。临时执行提示会自动收起，避免长期遮挡页面。
