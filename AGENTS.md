# codex-memory

这是Agent的本地长期记忆服务。本目录是源码；安装目录以install.py的--target为准，实际记忆只放在目标目录/data，不放进开发目录或Git。

先读README.md和USER_GUIDE.md。正式Markdown笔记是写入源，sources是依据，SQLite只做可重建索引。修改保存/更正/遗忘或范围过滤时，核对所有CLI/MCP调用者和test_store/test_mcp，保留原子写入、版本冲突、遗忘抑制和出处。

GUI入口gui.py复用MemoryStore，ui/只呈现数据，shortcuts.py创建pythonw快捷方式。面板供本机用户管理全库，不能把GUI管理范围套到Agent检索上；CLI/MCP继续按当前project过滤。服务仅绑定回环，保留令牌、Host/Origin校验、textContent渲染和并发更正校验。

外观、动效和缩放保存在data/ui-settings.json；只能经校验后的preferences入口写入，不把界面设置当成正式记忆。动效须可中断，关闭后终止现行动画；主题色需要检查文字对比度。截图与测试只用browser_fixture.py的临时虚构资料。

native_caption.py通过标准库本地JSON管道启动独立Edge进程，再用PWA协议打开本服务的回环地址；HTTP不接受任意浏览器命令或窗口句柄。使用官方Window Controls Overlay，按窗口安全区预留顶部按钮空间，不裁切或嵌入窗口，不使用WebView2。独立配置在data/ui-edge，gui-port.json保存首选端口；不得改动日常浏览器配置、其他窗口或全局主题。PWA.launch返回tab容器，测试的Runtime操作要连接本服务URL对应的page目标。原生测试test_native_window.py须显式开启，仅用临时空资料；完整窗口截图不得裁剪或含真实记忆。检查冷启动单窗口、浅深色实际像素、缩放安全区、最大化/还原、重复打开与关闭后重开。

验证：python -X utf8 -m unittest discover -v；界面回归：node test_browser.mjs，运行时路径按本机配置，browser_fixture.py只创建临时虚构资料。部署：python -X utf8 install.py。安装只管理FILES中的代码和自己的MCP/AGENTS块，保留用户原配置、其他MCP和data。SDK目前复用本机mcp 2.1.0；不在源码硬编码API Key，不额外调用模型。

1.5.1的GUI后台持续运行；--background仅启动服务，不能打开面板。自启须用户授权，仅用当前用户Startup里的自有.lnk；不得写系统自启或自动固定任务栏。windows_taskbar.py通过标准Windows属性存储设置自己创建窗口的AppUserModelID与Python重启命令，必须核对进程和窗口归属，清理前再次验证PID/ID。原生测试manifest应用名必须唯一，不能与正式程序同名安装/卸载PWA；检查真正的任务栏重启命令而非只测PWA.launch。
