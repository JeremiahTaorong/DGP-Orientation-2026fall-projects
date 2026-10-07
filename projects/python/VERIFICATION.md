# Python 路线交付核对

按 `common/tasks.md` 和 `common/protocol.md` 的现行要求核对。选择 Python 路线；同步客户端和异步服务端为必做，同步服务端为已完成的可选层。三个任务目录各有独立 `pyproject.toml`、`uv.lock`、源码和测试。

| 要求 | 实现与核查 |
| --- | --- |
| 完整接口 | 两个服务端实现协议的 10 个接口；各自的 `test_complete_http_flow` 覆盖完整账号与文本流程，异步服务端另以真实 localhost HTTP 进程跑通 `ping → echo → register → login → put → list → get → delete → logout`。 |
| 客户端交互 | `test_read_text` 覆盖空文本、末尾换行和字面量 `:end`；请求构造、401 清理令牌、账号注销、非 JSON 错误及网络失败恢复由 `test_client.py` 覆盖。 |
| 身份与数据 | 两个服务端的业务和 HTTP 测试覆盖重复注册、错误凭据、登录替换、退出、注销后同名重注册、用户间隔离、列表排序与固定期限令牌。 |
| 输入和错误 | 测试覆盖字段类型与范围、未知路径和错误方法、非法 JSON、文本 UTF-8 字节上限、整个请求体上限及相应 HTTP 状态。 |
| 并发与异步 | 两个服务端均测试同名注册、并发登录、登录与退出交错、注销与旧请求交错；异步 HTTP 测试证实阻塞业务期间事件循环仍可响应 `/ping`。 |
| 参考程序 | 使用官方 Windows `reference-v0.2.1` 双向联调：本客户端连接参考服务端，参考客户端连接本异步服务端。结果记录在 `IMPLEMENTATION.md`。 |
| 提交与复现 | 依赖锁文件和分步 Git 提交保留；`.venv`、缓存、参考二进制和凭证不入库。启动及检查命令记录在 `IMPLEMENTATION.md`。 |

## 本机最终检查

2026-10-07，在每个任务目录使用各自虚拟环境的 Python 执行 `pytest`，用本地 Ruff 和 Pyright 执行静态检查：

| 任务 | pytest | Ruff lint / format | Pyright |
| --- | --- | --- | --- |
| `client-sync` | 18 passed | 通过 / 通过 | 0 errors |
| `server-sync` | 54 passed | 通过 / 通过 | 0 errors |
| `server-async` | 55 passed | 通过 / 通过 | 0 errors |

Windows 应用控制阻止了 `uv.exe` 启动；本机检查和运行改用已创建虚拟环境的官方 Python 模块入口，未更改系统安全策略。同步服务端测试有一条固定版本 Starlette/AnyIO 的弃用提示，不影响通过结果。
