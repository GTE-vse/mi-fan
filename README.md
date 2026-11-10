# AirLink 3.1.2 分享版源码

Windows 10 1809+ / Windows 11 x64，React 19 + Material UI 7 + pywebview 5.4 / WebView2，Python 3.9。
支持 zhimi.airpurifier.m1。首次默认设备信息为空，允许先保存温度和外观设置；填写自己的设备信息后才可控制。

3.1.2 新增：首次进入设备页、显式启动独立 Token 工具、自动选择 CPU 温度、附带 Libre 相对路径、微软运行库检查与安装引导。
保留温度曲线、趋势图提示、停止恢复自动模式以及管理 Libre 生命周期的功能。

## 构建

```powershell
py -3.9 -m venv .venv
& ./.venv/Scripts/python.exe -m pip install --upgrade pip
& ./.venv/Scripts/python.exe -m pip install --no-deps -r requirements-build.txt
Push-Location frontend
npm ci --ignore-scripts
Pop-Location
& ./.venv/Scripts/python.exe ./build_frontend.py
& ./.venv/Scripts/python.exe ./build_exe.py ./dist
```

运行目录应带有 LibreHardwareMonitor/、工具/token_extractor.exe 和运行库/。
运行库引导文件名及固定 SHA-256 见 backend/portable_runtime.py，下载地址见便携包的第三方许可/运行库来源.json。
运行库只在缺少环境且用户同意后执行。构建和测试不会执行提取工具或微软安装器。
requirements 显式包含 netifaces-plus，不要额外安装依赖旧 netifaces 的 python-miio 环境。
frontend/dist 为已编译资源。修改源码后的前端构建需要安装 node_modules，源码包不含开发缓存。

## 验证

```powershell
& ./.venv/Scripts/python.exe -B -m unittest discover -s tests -p "test_*.py"
Push-Location frontend
node test-dom.mjs
node test-features.mjs
node test-portable.mjs
Pop-Location
```

42 项后端测试和 3 组前端 DOM 测试覆盖联动、曲线、历史提示、Libre 生命周期、温度选择、路径迁移和首次配置。
可用 AirLink.exe --package-check <输出文件路径> 进行无设备操作的启动检查；不会开启窗口、启动 Libre、运行安装器或读取 Token 工具。
已在工具环境验证的内容详见分享包旁的打包验证报告；未在另一台干净 Windows 电脑上完成全流程测试。

## 第三方代码

开发源码.zip 还包含 third_party/python-miio 的已使用 Python 源码及许可证。
其他依赖的版本、许可证和上游源码入口见 requirements-build.txt、frontend/package-lock.json 与分享包的第三方许可目录。
Libre 为 0.9.6，原始源码提交 3d331e3370efb858411f19511373eff65a218701。
Token 提取 EXE 是分享者提供的原文件，未反编译重建或确认其上游版本。
