from __future__ import annotations

import threading
import traceback
import webbrowser
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from bid_upload_assistant.controller import UploadController
from bid_upload_assistant.files import PROJECT_SELF, bid_root, find_bidders


WORKSPACE = Path(__file__).resolve().parent
app = Flask(__name__)
controller = UploadController(WORKSPACE)
state_lock = threading.RLock()
batch_running = False
batch_message = "尚未连接编制工具。"


def payload() -> dict[str, Any]:
    return request.get_json(silent=True) or request.form.to_dict()


def choose(kind: str, initial: str = "") -> str:
    """由本机服务弹出 Windows 选择框，浏览器不需要手输长路径。"""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "project":
            return filedialog.askdirectory(title="选择项目文件夹", initialdir=initial or str(WORKSPACE))
        if kind == "zjzbs":
            return filedialog.askopenfilename(title="选择 .zjzbs 招标文件", initialdir=initial or str(WORKSPACE), filetypes=[("招标文件", "*.zjzbs")])
        if kind == "boq":
            return filedialog.askopenfilename(
                title="选择工程量清单", initialdir=initial or str(WORKSPACE),
                filetypes=[("工程量清单", "*.tbfjgcl *.tbjtgcl *.tbslgcl *.tbsygcl *.tbszgcl *.tbyllhgcl *.xlsx *.xls *.pdf")],
            )
        return filedialog.askopenfilename(title="选择成员资料表", initialdir=initial or str(WORKSPACE), filetypes=[("成员资料", "*.csv *.xlsx *.xls")])
    finally:
        root.destroy()


def snapshot() -> dict[str, Any]:
    return {
        "message": batch_message,
        "batchRunning": batch_running,
        "pilotVerified": controller.pilot_verified,
        "projectId": controller.project_id,
        "chapterCount": len(controller.chapters),
        "items": [item.to_dict() for item in controller.items],
        "members": controller.members,
        "memberCount": len(controller.members),
        "memberSources": controller.member_sources,
        "bidderProfile": controller.bidder_profile,
        "bidderAgent": controller.bidder_agent,
        "bidderSource": controller.bidder_source,
        "boqVerified": controller.boq_verified,
        "boqPath": controller.boq_path,
    }


def ok(**extra: Any):
    return jsonify({"ok": True, **extra, "state": snapshot()})


def failed(message: str, status: int = 400):
    return jsonify({"ok": False, "message": message, "state": snapshot()}), status


@app.get("/")
def index():
    return render_template("index.html")


@app.route("/api/state", methods=["GET", "POST"])
def get_state():
    with state_lock:
        return ok()


@app.post("/api/choose/<kind>")
def native_choose(kind: str):
    if kind not in {"project", "zjzbs", "members", "boq"}:
        return failed("未知选择类型")
    try:
        with state_lock:
            selected = choose(kind, str(payload().get("initial") or ""))
            result: dict[str, Any] = {"path": selected}
            if kind == "project" and selected:
                bidders = find_bidders(Path(selected))
                result["bidders"] = bidders
            return ok(**result)
    except Exception as exc:
        return failed(str(exc))


@app.post("/api/read-chapters")
def read_chapters():
    global batch_message
    data = payload()
    try:
        with state_lock:
            if batch_running:
                return failed("批量任务正在运行，不能切换项目")
            bidder = str(data.get("bidder") or "")
            controller.set_input(str(data.get("projectDir") or ""), bidder, str(data.get("zjzbsPath") or ""))
            if controller.gateway:
                controller.gateway.close()
            controller.connect(str(data.get("endpoint") or "http://127.0.0.1:9222"))
            controller.import_and_read_chapters()
            batch_message = f"已读取软件实际章节树：{len(controller.chapters)} 个章节。请检查匹配，先做一条单文件验证。"
            return ok()
    except Exception as exc:
        batch_message = "读取已停止：" + str(exc)
        return failed(str(exc))


@app.post("/api/launch-tool")
def launch_tool():
    global batch_message
    try:
        with state_lock:
            controller.launch_tool(str(payload().get("exePath") or ""))
            batch_message = "已请求以调试模式启动编制工具；软件打开后再读取章节。"
            return ok()
    except Exception as exc:
        return failed(str(exc))


@app.post("/api/choose-source")
def choose_source():
    data = payload()
    try:
        with state_lock:
            if not controller.project_dir:
                return failed("请先读取项目")
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
            try:
                selected = filedialog.askopenfilename(
                    title="选择该章节的成品 PDF/Word",
                    initialdir=str(bid_root(controller.project_dir, controller.bidder)),
                    filetypes=[("可上传文件", "*.pdf *.doc *.docx")],
                )
            finally:
                root.destroy()
            if selected:
                controller.set_manual_source(str(data.get("chapterCode") or ""), selected)
            return ok(path=selected)
    except Exception as exc:
        return failed(str(exc))


@app.post("/api/verify-one")
def verify_one():
    global batch_message
    try:
        with state_lock:
            item = controller.verify_one(str(payload().get("chapterCode") or ""))
            batch_message = f"单文件结果：{item.status}。{item.reason}"
            return ok()
    except Exception as exc:
        batch_message = "单文件验证已停止：" + str(exc)
        return failed(str(exc))


@app.post("/api/advance-one")
def advance_one():
    global batch_message
    try:
        with state_lock:
            if batch_running:
                return failed("批量任务正在运行，请先点停止")
            item = controller.advance_one(str(payload().get("chapterCode") or ""))
            batch_message = f"单章结果：{item.chapter_title} 已真实点“下一章”并读回完成。"
            return ok()
    except Exception as exc:
        batch_message = "单章执行已停止：" + str(exc)
        return failed(str(exc))


@app.post("/api/item/skip")
def skip_item():
    global batch_message
    try:
        with state_lock:
            if batch_running:
                return failed("批量任务正在运行，请先点停止")
            item = controller.skip_item(str(payload().get("chapterCode") or ""))
            batch_message = f"已将“{item.chapter_title}”标记为跳过；执行时会真实点“下一章”。"
            return ok()
    except Exception as exc:
        return failed(str(exc))


@app.post("/api/item/retry")
def retry_item():
    global batch_message
    try:
        with state_lock:
            if batch_running:
                return failed("批量任务正在运行，请先点停止")
            item = controller.retry_item(str(payload().get("chapterCode") or ""))
            batch_message = f"已将“{item.chapter_title}”排队重试。"
            return ok()
    except Exception as exc:
        return failed(str(exc))


def batch_worker() -> None:
    global batch_running, batch_message
    try:
        # 不占用状态锁：停止按钮必须能在当前文件处理期间发出中断请求。
        controller.run_batch()
        report = controller.write_report("batch")
        completed = sum(1 for item in controller.items if item.chapter_submitted)
        batch_message = f"批量任务结束：{completed}/{len(controller.items)} 个小章节已真实点“下一章”并读回完成。报告：{report}"
    except Exception as exc:
        batch_message = "批量任务异常停止：" + str(exc)
        traceback.print_exc()
    finally:
        batch_running = False


@app.post("/api/run-batch")
def run_batch():
    global batch_running, batch_message
    with state_lock:
        if batch_running:
            return failed("批量任务已经在运行")
        if not controller.pilot_verified:
            return failed("单文件尚未真实核验成功，批量上传仍被锁定")
        batch_running = True
        batch_message = "批量任务启动；每条都会上传回读、选中小章节、真实点“下一章”，再回读完成状态。"
        threading.Thread(target=batch_worker, daemon=True).start()
        return ok()


@app.post("/api/stop")
def stop():
    global batch_message
    with state_lock:
        controller.request_stop()
        batch_message = "已请求停止；当前软件调用结束后不会开始下一条。"
        return ok()


@app.post("/api/members/load")
def load_members():
    global batch_message
    try:
        with state_lock:
            path = str(payload().get("path") or "")
            if not path:
                path = choose("members")
            if not path:
                return ok(path="")
            controller.load_member_sheet(path)
            batch_message = f"已载入并校验成员资料：{len(controller.members)} 人。"
            return ok(path=path)
    except Exception as exc:
        return failed(str(exc))


@app.post("/api/bidder/extract")
def extract_bidder():
    global batch_message
    try:
        with state_lock:
            controller.extract_bidder_from_project()
            batch_message = "已从成品 PDF 识别投标人、法人、联系人和委托代理人；尚未写入软件，请先核对预览。"
            return ok()
    except Exception as exc:
        return failed(str(exc))


@app.post("/api/bidder/write")
def write_bidder():
    global batch_message
    try:
        with state_lock:
            controller.write_bidder_and_verify()
            batch_message = "投标人基本资料已写入软件，并从投标人和项目记录分别读回核验。"
            return ok()
    except Exception as exc:
        return failed(str(exc))


@app.post("/api/members/extract")
def extract_members():
    global batch_message
    try:
        with state_lock:
            members = controller.extract_members_from_project()
            batch_message = f"已从成品 PDF 自动识别 {len(members)} 人；尚未写入软件，请先核对预览。"
            return ok()
    except Exception as exc:
        return failed(str(exc))


@app.post("/api/members/write")
def write_members():
    global batch_message
    try:
        with state_lock:
            controller.write_members_and_verify()
            batch_message = f"成员信息已写入软件并重新读取核验：{len(controller.members)} 人。"
            return ok()
    except Exception as exc:
        return failed(str(exc))


@app.post("/api/members/template")
def member_template():
    try:
        with state_lock:
            path = controller.export_member_template()
            return ok(path=str(path))
    except Exception as exc:
        return failed(str(exc))


@app.post("/api/boq/import")
def import_boq():
    global batch_message
    try:
        with state_lock:
            path = str(payload().get("path") or "")
            if not path:
                path = choose("boq", str(controller.project_dir or WORKSPACE))
            if not path:
                return ok(path="")
            controller.import_boq_and_verify(path)
            batch_message = "工程量清单已导入，软件重新读取核验成功。"
            return ok(path=path)
    except Exception as exc:
        return failed(str(exc))


@app.post("/api/legal-date")
def save_legal_date():
    global batch_message
    try:
        with state_lock:
            item = controller.save_legal_date_and_verify(str(payload().get("date") or ""))
            batch_message = item.reason
            return ok()
    except Exception as exc:
        return failed(str(exc))


@app.post("/api/manual/verify")
def verify_manual():
    global batch_message
    try:
        with state_lock:
            item = controller.verify_manual_chapter(str(payload().get("chapterCode") or ""))
            batch_message = item.reason
            return ok()
    except Exception as exc:
        batch_message = "人工填写章节尚未通过核验：" + str(exc)
        return failed(str(exc))


def main() -> None:
    url = "http://127.0.0.1:8765"
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=False)


if __name__ == "__main__":
    main()
