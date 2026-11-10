import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time

APP_DIR=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--package-check':
        from portable_runtime import prerequisites
        from api import Api
        check_api=Api(APP_DIR,monitoring=False)
        result={'version':'3.1.2','runtime':prerequisites(),
                'device_unconfigured':not check_api.config['token'] and not check_api.config['ip'],
                'libre_path_is_local':Path(check_api.config['lhm_path']).parent.parent==APP_DIR,
                'auto_temperature':check_api.config['sensor_name']=='auto'}
        Path(sys.argv[2]).write_text(json.dumps(result),encoding='utf-8')
        raise SystemExit(0)
    # This helper must run before the UI mutex, imports or monitoring are started.
    if len(sys.argv)==6 and sys.argv[1]=='--manage-libre':
        from libre_process import supervise
        try:
            raise SystemExit(supervise(int(sys.argv[2]),int(sys.argv[3]),sys.argv[4],sys.argv[5]))
        except Exception:
            raise SystemExit(1)
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        smoke='--smoke-test' in sys.argv
        if smoke: ctypes.windll.kernel32.SetErrorMode(0x0002)
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]
        kernel.CreateMutexW.restype=ctypes.c_void_p
        mutex=kernel.CreateMutexW(None,False,'Local\\MiFanSmoke' if smoke else 'Local\\MiFanDesktop')
        if ctypes.get_last_error()==183:
            ctypes.windll.user32.MessageBoxW(None,'Mi Fan 已经在运行，请查看已有窗口。','Mi Fan',0x40)
            raise SystemExit(0)
        if not smoke:
            from portable_runtime import ensure_runtime
            if not ensure_runtime(APP_DIR):
                raise SystemExit(0)
        import webview
        from api import Api,Bridge,safe_close
        api=Api(APP_DIR,monitoring=not smoke)
        bundle=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
        html=bundle/'frontend/dist/index.html'
        window=webview.create_window('Mi Fan · 米饭 — 小米空气净化器控制器',str(html),js_api=Bridge(api),
                    width=1180,height=820,min_size=(1050,730),background_color='#0a0a0c')
        api.window=window
        try:
            icon_path=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))/'mi_fan.ico'
            if icon_path.is_file(): window.icon=str(icon_path)
        except Exception: pass
        # 托盘预初始化：如果偏好里开了 tray_minimize，启动就先把托盘图标跑起来
        if api.config.get('tray_minimize'):
            threading.Thread(target=api.init_tray,daemon=True).start()
        # 关闭事件：托盘常驻时隐藏窗口，否则正常退出
        def _on_closing():
            try:
                if api.handle_minimize_to_tray():
                    # pystray 托盘已起来，立即 hide 窗口
                    api.hide_window()
                    return False  # 阻止关闭 ✅
            except Exception as exc:
                api.log('托盘流程异常，允许正常关闭：%s'%exc)
            # 正常退出
            if api.shutdown_event.is_set(): return True
            with api.lock:
                if api.closing: return True
                api.closing=True
            try: api.shutdown_tray()  # 停托盘
            except: pass
            def finish():
                try: api.shutdown()
                except Exception as exc: api.failure(exc)
            threading.Thread(target=finish,daemon=True).start()
            return True  # 允许关闭
        window.events.closing += _on_closing
        # 最小窗口尺寸 1050×730（与默认 1180×820 同比例 ~1.44:1），小于此尺寸 UI 会崩
        # 不强制 aspect ratio——用户爱怎么拉怎么拉，只要不小于最小尺寸就行
        # 深色模式下改标题栏颜色（Windows 10 1803+ / Windows 11）
        try:
            def _set_caption():
                hwnd=ctypes.windll.user32.FindWindowW(None,'Mi Fan · 米饭 — 小米空气净化器控制器')
                if hwnd:
                    DWMWA_CAPTION_COLOR=35
                    DWMWA_TEXT_COLOR=36
                    # 0x00RRGGBB 格式（低位字节是 B）—— 深色 #141418
                    dark_caption=0x00181414
                    light_caption=0x00FFF8F0
                    # 默认深色，用户切浅色时前端会发消息过来
                    for attr,val in [(DWMWA_CAPTION_COLOR,dark_caption),(DWMWA_TEXT_COLOR,0x00E8E8EC),(37,1)]:  # 37=DWMWA_CAPTION_COLOR_POLICY, 1=启用
                        try: ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd,attr,ctypes.byref(ctypes.c_int(val)),4)
                        except: pass
            window.events.shown+=_set_caption
        except Exception: pass
        def verify():
            deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                try:
                    ready=window.evaluate_js('Boolean(document.querySelector("[data-testid=\"dashboard\"]"))')
                    if ready:
                        report=window.evaluate_js('JSON.stringify({title:document.title,dpr:devicePixelRatio,text:document.body.innerText,overflow:document.documentElement.scrollWidth>innerWidth})')
                        (APP_DIR/'smoke-test.json').write_text(report,encoding='utf-8')
                        api.shutdown(); window.destroy();return
                except Exception: pass
                time.sleep(0.25)
            (APP_DIR/'smoke-test.json').write_text('{"error":"UI did not become ready"}',encoding='utf-8')
            api.shutdown(); window.destroy()
        webview.start(verify if smoke else None,gui='edgechromium',debug=False,
                      storage_path=str(APP_DIR/'.webview'),private_mode=True)
    except Exception:
        import traceback
        text=traceback.format_exc()
        (APP_DIR/'startup-error.log').write_text(text,encoding='utf-8')
        ctypes.windll.user32.MessageBoxW(None,'启动失败，详情已写入程序目录 startup-error.log。','Mi Fan',0x10)
