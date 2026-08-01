from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

from .models import Chapter


class GatewayError(RuntimeError):
    pass


@dataclass
class BridgeReply:
    success: bool
    data: Any = None
    message: str = ""


class CdpTenderGateway:
    """经 CEF DevTools 调用软件自身 jsBridge；不模拟通用“上传”按钮。"""

    def __init__(self, endpoint: str = "http://127.0.0.1:9222") -> None:
        self.endpoint = endpoint.rstrip("/")
        self._ws = None
        self._seq = 0

    def connect(self) -> None:
        try:
            import websocket  # type: ignore
        except ImportError as exc:
            raise GatewayError("缺少 websocket-client，请先安装 requirements.txt") from exc
        try:
            with urllib.request.urlopen(self.endpoint + "/json/list", timeout=4) as response:
                pages = json.load(response)
        except Exception as exc:
            raise GatewayError("无法连接编制工具调试端口。请以管理员身份用 --remote-debugging-port=9222 启动软件。") from exc
        page = next((x for x in pages if x.get("type") == "page"), None)
        if not page or not page.get("webSocketDebuggerUrl"):
            raise GatewayError("编制工具没有可调试的 CEF 页面")
        self._ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=15)
        self._eval("window.jsBridge ? true : false")
        if not self._eval("window.jsBridge ? true : false"):
            raise GatewayError("CEF 页面未暴露 window.jsBridge")

    def close(self) -> None:
        if self._ws:
            self._ws.close()
            self._ws = None

    def _eval(self, expression: str) -> Any:
        if not self._ws:
            raise GatewayError("尚未连接编制工具")
        self._seq += 1
        request_id = self._seq
        self._ws.send(json.dumps({
            "id": request_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "awaitPromise": True, "returnByValue": True},
        }))
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            payload = json.loads(self._ws.recv())
            if payload.get("id") != request_id:
                continue
            if "error" in payload:
                raise GatewayError(str(payload["error"]))
            result = payload.get("result", {}).get("result", {})
            if result.get("subtype") == "error":
                raise GatewayError(result.get("description", "CEF 执行失败"))
            return result.get("value")
        raise GatewayError("等待编制工具响应超时")

    def call(self, namespace: str, action: str, payload: dict[str, Any]) -> BridgeReply:
        args = json.dumps(payload, ensure_ascii=False)
        expression = f"""(async () => {{
          if (!window.jsBridge || typeof window.jsBridge[{json.dumps(namespace)}] !== 'function') {{
            return {{success:false, message:'jsBridge 不可用'}};
          }}
          return await window.jsBridge[{json.dumps(namespace)}]({json.dumps(action)}, {args});
        }})()"""
        result = self._eval(expression)
        if not isinstance(result, dict):
            raise GatewayError(f"软件返回异常: {result!r}")
        return BridgeReply(bool(result.get("success")), result.get("data"), result.get("msg") or result.get("message") or "")

    def method(self, name: str, *arguments: Any) -> BridgeReply:
        encoded = ", ".join(json.dumps(arg, ensure_ascii=False) for arg in arguments)
        expression = f"""(async () => {{
          if (!window.jsBridge || typeof window.jsBridge[{json.dumps(name)}] !== 'function') {{
            return {{success:false, message:'jsBridge 方法不可用: {name}'}};
          }}
          return await window.jsBridge[{json.dumps(name)}]({encoded});
        }})()"""
        result = self._eval(expression)
        if not isinstance(result, dict):
            raise GatewayError(f"软件返回异常: {result!r}")
        return BridgeReply(bool(result.get("success")), result.get("data"), result.get("msg") or result.get("message") or "")

    def import_zjzbs(self, file_path: str) -> BridgeReply:
        return self.call("project", "create", {"filePath": file_path})

    def project_info(self, project_id: str) -> BridgeReply:
        return self.call("project", "info", {"id": project_id})

    def bidder_info(self) -> BridgeReply:
        return self.call("bidder", "info", {})

    def update_bidder(self, bidder_data: dict[str, Any]) -> BridgeReply:
        return self.call("bidder", "update", bidder_data)

    def list_projects(self) -> BridgeReply:
        return self.call("project", "list", {"pageNum": 1, "pageSize": 100})

    def save_project_draft(self, project_data: dict[str, Any]) -> BridgeReply:
        return self.call("project", "saveDraft", {"type": "project", "data": project_data})

    def navs(self, tbs_id: str) -> BridgeReply:
        return self.call("tbs", "navs", {"tbsId": tbs_id})

    def chapter_list(self, tbs_id: str, nav_code: str) -> BridgeReply:
        return self.call("tbs", "chapterList", {"tbsId": tbs_id, "navCode": nav_code})

    def add_file(self, file_path: str) -> BridgeReply:
        return self.call("file", "add", {"filePath": file_path})

    def convert_to_tool_pdf(self, file_path: str) -> BridgeReply:
        return self.method("wordToPdfJar", file_path)

    def replace_chapter(self, tbs_id: str, chapter_code: str, link: str, pdf_link: str) -> BridgeReply:
        return self.call("tbs", "replaceChapterContent", {
            "tbsId": tbs_id,
            "chapterCode": chapter_code,
            "link": link,
            "pdfLink": pdf_link,
        })

    def restore_nav_chapters(self, tbs_id: str, nav_code: str) -> BridgeReply:
        """调用软件界面“恢复文件”所使用的原生接口，仅用于可回滚测试。"""
        return self.call("tbs", "resetChapters", {"tbsId": tbs_id, "chapterCode": nav_code})

    def tbs_info(self, tbs_id: str) -> BridgeReply:
        return self.call("tbs", "info", {"id": tbs_id})

    def import_boq(self, project_id: str, raw_path: str, staged_path: str) -> BridgeReply:
        return self.call("tbs", "checkBoq", {
            "rawFilePath": raw_path,
            "filePath": staged_path,
            "projectId": project_id,
        })

    def chapter_key_data(self, project_id: str, tbs_id: str) -> BridgeReply:
        return self.call("tbs", "chapterKeyData", {"projectId": project_id, "tbsId": tbs_id})

    def save_fields(self, tbs_id: str, nav_code: str, fields: list[dict[str, Any]], status: str = "draft") -> BridgeReply:
        return self.call("tbs", "fieldSave", {
            "tbsId": tbs_id,
            "chapterCode": nav_code,
            "fields": fields,
            "update": {"chapterStatus": status},
        })

    def set_chapter_submit_status(self, tbs_id: str, chapter_code: str, value: str) -> BridgeReply:
        return self.call("tbs", "setChapterSubmitStatus", {
            "tbsId": tbs_id,
            "chapterCode": chapter_code,
            "isSubmit": {"value": value},
        })

    def click_visible_next_chapter(
        self, project_id: str, root_code: str, chapter_title: str,
    ) -> BridgeReply:
        """在真实编制工具页面选中小章节并点击“下一章”。

        这一步故意不直接调用 setChapterSubmitStatus：软件自己的按钮还会
        保存当前表单、刷新目录并移动到下一小节，用户也能看到操作过程。
        最终是否成功必须由控制器重新读取该小章节的 isSubmit 核验。
        """
        route = f"#/tender/{root_code}/file-tender/{project_id}"
        current = str(self._eval("location.hash") or "")
        if current != route:
            self._eval(f"location.hash={json.dumps(route, ensure_ascii=False)}; true")

        deadline = time.monotonic() + 20
        selected = False
        while time.monotonic() < deadline:
            result = self._eval(f"""(() => {{
              const norm = value => String(value || '').replace(/\\s+/g, '');
              const wanted = norm({json.dumps(chapter_title, ensure_ascii=False)});
              const nodes = Array.from(document.querySelectorAll('.ant-tree-treenode'));
              const node = nodes.find(item => {{
                const title = item.querySelector('.szzj-tree-node-title, .ant-tree-title');
                return title && norm(title.innerText) === wanted;
              }});
              if (!node) return 'waiting-tree';
              node.scrollIntoView({{block:'center'}});
              const clickable = node.querySelector('.ant-tree-node-content-wrapper') || node;
              clickable.click();
              return 'selected';
            }})()""")
            if result == "selected":
                selected = True
                break
            time.sleep(0.25)
        if not selected:
            return BridgeReply(False, message=f"真实界面中找不到小章节：{chapter_title}")

        # 等 React/Formily 完成章节切换，避免点到上一个章节的按钮状态。
        time.sleep(0.8)
        clicked = self._eval("""(() => {
          const button = Array.from(document.querySelectorAll('button')).find(
            item => item.offsetParent !== null && item.innerText.trim() === '下一章'
          );
          if (!button || button.disabled) return false;
          button.click();
          return true;
        })()""")
        if not clicked:
            return BridgeReply(False, message="真实界面中的“下一章”不可点击")
        return BridgeReply(True, {"route": route, "chapterTitle": chapter_title})

    def visible_form_errors(self) -> list[str]:
        value = self._eval("""Array.from(document.querySelectorAll(
          '.ant-formily-item-error-help,.ant-form-item-explain-error,.ant-message-notice-content,.ant-modal-content'
        )).filter(item => item.offsetParent !== null)
          .map(item => item.innerText.trim()).filter(Boolean)""")
        return [str(item) for item in value] if isinstance(value, list) else []

    def visible_chapter_marker(self, chapter_title: str) -> dict[str, Any]:
        value = self._eval(f"""(() => {{
          const norm = value => String(value || '').replace(/\\s+/g, '');
          const wanted = norm({json.dumps(chapter_title, ensure_ascii=False)});
          const node = Array.from(document.querySelectorAll('.ant-tree-treenode')).find(item => {{
            const title = item.querySelector('.szzj-tree-node-title, .ant-tree-title');
            return title && norm(title.innerText) === wanted;
          }});
          const icon = node && node.querySelector('[aria-label="check-circle"]');
          if (!icon) return {{found:false, done:false}};
          const className = String(icon.className || '');
          const color = getComputedStyle(icon).color;
          return {{
            found: true,
            done: className.includes('doneIcon') && !className.includes('noIcon'),
            className,
            color
          }};
        }})()""")
        return value if isinstance(value, dict) else {"found": False, "done": False}


def parse_chapters(raw: Any) -> list[Chapter]:
    result: list[Chapter] = []

    def walk(nodes: Any, parent: str | None = None) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            try:
                config = json.loads(node.get("chapterConfig") or "{}")
            except json.JSONDecodeError:
                config = {}
            try:
                content = json.loads(node.get("chapterContent") or "{}")
            except json.JSONDecodeError:
                content = {}
            code = str(node.get("chapterCode") or node.get("key") or "")
            if code:
                result.append(Chapter(
                    code=code,
                    title=str(node.get("chapterTitle") or node.get("chapterName") or node.get("text") or code),
                    parent_code=str(node.get("parentChapterCode") or parent or "") or None,
                    allow_upload=bool(config.get("allowUpload")),
                    disabled_upload=bool(config.get("disabledUpload")),
                    # 软件实际把替换后的链接放在 chapterContent 中；顶层
                    # pdfLink 并不随 replaceChapterContent 更新。
                    existing_pdf_link=str(node.get("pdfLink") or content.get("pdfLink") or ""),
                    replace_status=str((node.get("replaceStatus") or {}).get("value", node.get("replaceStatus") or "")),
                    raw=node,
                ))
            walk(node.get("children"), code or parent)

    walk(raw)
    return result
