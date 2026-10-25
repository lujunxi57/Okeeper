# 用独立 Docker 体验首次使用

这个环境用于体验开源版的欢迎页、环境检测和账号管理。容器预装 Codex，使用全新的用户目录，不读取主机的登录、聊天记录或项目配置。额度读取与后台定时执行尚未接入，页面会标明演示内容。

## 启动

在仓库根目录运行：

```sh
docker compose --env-file /dev/null -f compose.lab.yaml up -d --build app
```

打开 **http://127.0.0.1:18795**。默认只映射本地端口，不需要 Tailscale。已使用 rootless Docker 时，在命令中将 `docker` 替换为 `docker --context rootless`。

首页保留 main 的米白绿色风格和动态 Logo。动态小人位于页面内容中，右下角是 GitHub 图标；点击小人，或聚焦后按 Enter / 空格，可以切换动作。

点击“开始配置”检查容器中的 Codex 安装和组件响应情况，然后进入工作台。容器首次启动没有账号，可以在“管理账号”中通过官方页面登录添加。检测结果只说明本地组件能响应，不代表已验证账号额度或完成模型调用。

## 账号与数据

只有 `lab-home` 数据卷保存容器用户目录，包括 Okeeper 账号记录和容器内的 Codex 登录；停止或重建容器后仍会保留。容器不挂载主机目录或 Docker 控制接口，其他项目的服务和账号不会被导入。

已有账号只能在用户明确要求后迁入。登录资料应直接写入运行容器的数据卷，不应放入镜像、构建目录或仓库。迁入的账号仅保存在本机数据卷中；新用户从本仓库构建时仍会得到没有账号的环境。

重新体验完全空白的环境时，可以使用新的 Compose 项目名创建另一套数据卷和端口，保留现有账号：

```sh
OKEEPER_LAB_PORT=18796 docker compose --env-file /dev/null -p okeeper-fresh -f compose.lab.yaml up -d --no-build app
```

## 常用操作

```sh
docker compose --env-file /dev/null -f compose.lab.yaml ps
docker compose --env-file /dev/null -f compose.lab.yaml logs --tail 50 app
docker compose --env-file /dev/null -f compose.lab.yaml stop app
```

更换本地端口时设置 `OKEEPER_LAB_PORT`；镜像中的 Codex 默认固定为 `0.164.0`，可通过 `OKEEPER_LAB_CODEX_VERSION` 修改后重新构建。仅测试“尚未安装 Codex”引导时，可设置 `OKEEPER_LAB_INSTALL_CODEX=0` 构建另一个测试环境。

服务在容器内监听 `18794`，通过 `--public-origin` 识别浏览器访问的映射端口。实际对用户提供的默认端口是 **18795**。
