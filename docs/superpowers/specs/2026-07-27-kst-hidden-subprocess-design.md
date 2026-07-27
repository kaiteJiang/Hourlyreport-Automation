# KST 子进程无窗口设计

## 问题

GUI 运行 KST 本地 API 时，每15秒刷新一次身份注册表。刷新过程会通过 `tasklist` 检查商务通进程，并使用 `OnlineWebCS.exe` 的 Node 模式读取推广 ID。报表请求也会使用同一 Electron 桥读取会话数据库。上述 `subprocess.run` 没有 Windows 无窗口标志，因此打包 GUI 会周期性闪出控制台窗口。

## 设计

新增单一的 KST 子进程参数函数：Windows 返回 `CREATE_NO_WINDOW`，其他系统不增加 Windows 参数。进程检测、推广 ID 读取和会话读取必须统一使用该参数；不改变命令、环境变量、超时和输出捕获。

运行中的 API 管理器继续每15秒检查登录状态，但复用已有 `KstIdentityRegistry`。注册表按安装根目录、身份和数据库路径缓存推广 ID 5分钟；新身份或数据库路径变化立即重新读取，缓存到期后也重新读取。这样既消除窗口，又减少 Electron 桥启动次数，不牺牲多账号变化检测。

## 验收

- Windows 子进程调用都包含 `CREATE_NO_WINDOW`。
- 同一注册表连续刷新只读取一次推广 ID。
- 缓存超过5分钟后重新读取。
- API 管理器刷新时复用已有注册表。
- KST 定向测试、完整测试、最终 EXE 构建和实机健康检查通过。
