from __future__ import annotations

import json
import ctypes
import hashlib
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .files import bid_root, prepare_pdf, scan_bid_files, score_match
from .bidder_extract import extract_bidder_profile
from .gateway import CdpTenderGateway, GatewayError, parse_chapters
from .members import equivalent, load_members, write_template
from .member_extract import extract_project_members
from .models import Chapter, RunReport, SourceFile, UploadItem


class UploadController:
    """所有会改变编制工具状态的操作集中在这里，并强制执行单文件门禁。"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.gateway: CdpTenderGateway | None = None
        self.project_dir: Path | None = None
        self.bidder = ""
        self.zjzbs_path: Path | None = None
        self.project_id = ""
        self.tbs_id = ""
        self.chapters: list[Chapter] = []
        self.sources: list[SourceFile] = []
        self.items: list[UploadItem] = []
        self.members: list[dict[str, str]] = []
        self.member_sources: list[str] = []
        self.bidder_profile: dict[str, object] = {}
        self.bidder_agent: dict[str, str] = {}
        self.bidder_source = ""
        self.boq_verified = False
        self.boq_path = ""
        self.pilot_verified = False
        self.stop_requested = False
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6]

    @property
    def output_dir(self) -> Path:
        return self.workspace / "output" / self.run_id

    @property
    def logs_dir(self) -> Path:
        return self.workspace / "logs"

    def connect(self, endpoint: str) -> None:
        self.gateway = CdpTenderGateway(endpoint)
        self.gateway.connect()

    @staticmethod
    def launch_tool(exe_path: str) -> None:
        """以管理员权限启动软件并开启 CEF 调试端口；不导入、不上传。"""
        executable = Path(exe_path)
        if not executable.is_file():
            raise ValueError(f"找不到编制工具: {executable}")
        try:
            import psutil
            if any((p.info.get("name") or "").lower() == executable.name.lower() for p in psutil.process_iter(["name"])):
                raise RuntimeError("编制工具已经在运行。请关闭它后再以调试模式启动，避免连接到错误页面。")
        except ImportError:
            pass
        args = "--remote-debugging-port=9222 --remote-allow-origins=http://127.0.0.1:9222"
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(executable), args, str(executable.parent), 1)
        if result <= 32:
            raise RuntimeError(f"启动编制工具失败，返回码: {result}")

    def request_stop(self) -> None:
        self.stop_requested = True

    def set_input(self, project_dir: str, bidder: str, zjzbs_path: str) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.zjzbs_path = Path(zjzbs_path).resolve()
        self.bidder = bidder
        if not self.project_dir.is_dir():
            raise ValueError("项目文件夹不存在")
        if not self.zjzbs_path.is_file() or self.zjzbs_path.suffix.lower() != ".zjzbs":
            raise ValueError("请选择存在的 .zjzbs 文件")
        self.sources = scan_bid_files(self.project_dir, bidder)
        if not self.sources:
            raise ValueError("所选单位的投标文件中没有可上传的 PDF/Word")

    def import_and_read_chapters(self) -> list[Chapter]:
        if not self.gateway or not self.project_dir or not self.zjzbs_path:
            raise GatewayError("请先连接软件并选择项目、单位和 .zjzbs")
        reply = self.gateway.import_zjzbs(str(self.zjzbs_path))
        if not reply.success:
            if "已导入" in reply.message:
                self.project_id = self._find_matching_project()
                if not self.project_id:
                    raise GatewayError("软件提示已导入，但未按完整路径找到同一测试项目；已停止")
            else:
                raise GatewayError("导入 .zjzbs 失败: " + reply.message)
        self.project_id = self.project_id or self._find_value(reply.data, "id") or self._find_value(reply.data, "projectId")
        if not self.project_id:
            self.project_id = self._find_matching_project()
        info = self.gateway.project_info(self.project_id)
        if not info.success:
            raise GatewayError("读取软件项目失败: " + info.message)
        self.tbs_id = self._find_value(info.data, "tbsId")
        if not self.tbs_id:
            raise GatewayError("软件项目没有返回 tbsId，不能安全读取章节")
        if isinstance(info.data, dict):
            actual_members = info.data.get("projectMemberList") or []
            if isinstance(actual_members, list):
                self.members = [dict(item) for item in actual_members if isinstance(item, dict)]
        tbs_info = self.gateway.tbs_info(self.tbs_id)
        if tbs_info.success and isinstance(tbs_info.data, dict):
            boq = tbs_info.data.get("boq") or {}
            solid = tbs_info.data.get("solidifyBoqList") or []
            if isinstance(boq, dict) and boq.get("path"):
                self.boq_verified = True
                self.boq_path = str(boq.get("fileName") or boq.get("path"))
            elif solid:
                self.boq_verified = True
                self.boq_path = "软件已有固化清单"
        nav_reply = self.gateway.navs(self.tbs_id)
        if not nav_reply.success:
            raise GatewayError("读取软件导航失败: " + nav_reply.message)
        # 当前软件版本的 navs 直接返回完整章节树；不能把树中的每一个
        # chapterCode 误当成 navCode 再请求，否则会漏章且无法做回读核验。
        chapters = parse_chapters(nav_reply.data)
        if not chapters:
            nav_codes = self._nav_codes(nav_reply.data)
            if not nav_codes:
                raise GatewayError("软件没有返回文档章节导航")
            for nav_code in nav_codes:
                reply = self.gateway.chapter_list(self.tbs_id, nav_code)
                if reply.success:
                    chapters.extend(parse_chapters(reply.data))
        unique = {chapter.code: chapter for chapter in chapters}
        self.chapters = list(unique.values())
        if not self.chapters:
            raise GatewayError("软件未返回章节树；停止，绝不上传")
        self.build_plan()
        self._write_event("chapter_discovery", {"chapter_count": len(self.chapters), "source_count": len(self.sources)})
        return self.chapters

    def build_plan(self) -> list[UploadItem]:
        used: set[str] = set()
        items: list[UploadItem] = []
        for chapter in self.chapters:
            if chapter.raw.get("children"):
                continue
            compact_title = chapter.title.replace(" ", "")
            if compact_title == "投标函":
                items.append(UploadItem(chapter.code, chapter.title, status="manual", reason="请在编制工具内人工填写；网页负责重新读取必填项核验", action="manual"))
                continue
            if compact_title == "法定代表人身份证明书":
                items.append(UploadItem(chapter.code, chapter.title, status="pending", reason="在网页选择日期并写入软件核验", action="date"))
                continue
            if compact_title == "目录":
                items.append(UploadItem(chapter.code, chapter.title, status="skip", reason="软件自动生成，无需导入", action="generated"))
                continue
            # 用户只需在流程开始时导入一次清单。商务标里的报价封面、
            # 报价页和已标价清单都由编制工具基于该清单生成，不能再因
            # 文件名相似而把“工程量清单报价说明.pdf”误当作章节附件。
            if compact_title in {"投标总报价封面", "工程量清单报价", "已标明价格的工程量清单"}:
                items.append(UploadItem(
                    chapter.code, chapter.title,
                    status="success" if self.boq_verified else "pending",
                    reason=("前置工程量清单已从软件读回，本章节由软件生成"
                            if self.boq_verified else "先导入工程量清单，本章节由软件自动生成"),
                    action="boq",
                ))
                continue
            if not chapter.allow_upload or chapter.disabled_upload:
                items.append(UploadItem(chapter.code, chapter.title, status="skip", reason="该章节不允许导入，保留给软件生成或直接继续", action="generated"))
                continue
            candidates = sorted(
                ((score_match(chapter.title, source), source) for source in self.sources),
                key=lambda pair: pair[0][0], reverse=True,
            )
            if not candidates or candidates[0][0][0] < 50:
                optional = "若有" in chapter.title
                items.append(UploadItem(
                    chapter.code, chapter.title,
                    status="skip",
                    reason=("投标文件中未找到对应成品，按“若有”规则跳过"
                            if optional else "项目内没有对应成品，将在真实界面点“下一章”跳过"),
                    action="optional_skip" if optional else "missing_skip",
                ))
                continue
            (score, reason), source = candidates[0]
            second_score = candidates[1][0][0] if len(candidates) > 1 else 0
            if str(source.path) in used or score - second_score < 8:
                items.append(UploadItem(chapter.code, chapter.title, status="pending", reason="匹配有歧义，请人工指定"))
                continue
            used.add(str(source.path))
            items.append(UploadItem(
                chapter.code, chapter.title, str(source.path), confidence="high" if score >= 85 else "medium", reason=reason, action="upload",
            ))
        by_code = {chapter.code: chapter for chapter in self.chapters}
        for item in items:
            chapter = by_code.get(item.chapter_code)
            if chapter and self._submit_value(chapter) == "1":
                item.chapter_submitted = True
                item.completion_evidence = "从软件章节树读回 isSubmit=1"
                if item.action == "upload" and chapter.existing_pdf_link:
                    item.status = "success"
                    item.reason = "软件已有附件，且该小章节已完成"
                    item.verified_link = chapter.existing_pdf_link
        self.items = items
        return items

    def set_manual_source(self, chapter_code: str, source_path: str) -> None:
        source = Path(source_path).resolve()
        if not self.project_dir or not source.is_file():
            raise ValueError("人工指定的文件不存在")
        allowed_root = bid_root(self.project_dir, self.bidder).resolve()
        if allowed_root not in source.parents:
            raise ValueError("只能选择该单位投标文件目录内的文件")
        item = next((x for x in self.items if x.chapter_code == chapter_code), None)
        if not item:
            raise ValueError("章节不存在")
        item.source_path = str(source)
        item.status = "pending"
        item.action = "upload"
        item.confidence = "manual"
        item.reason = "人工指定"

    def skip_item(self, chapter_code: str) -> UploadItem:
        item = self._item(chapter_code)
        if item.chapter_submitted:
            raise ValueError("该小章节已完成，不能再改为跳过")
        if item.action not in {"upload", "optional_skip", "missing_skip", "user_skip"}:
            raise ValueError("人工填写、日期和清单章节不能用通用跳过")
        item.action = "user_skip"
        item.status = "skip"
        item.reason = "用户在网页明确选择跳过；批处理时仍会真实点“下一章”"
        self._write_event("item_marked_skip", item.to_dict())
        return item

    def retry_item(self, chapter_code: str) -> UploadItem:
        item = self._item(chapter_code)
        if item.chapter_submitted:
            raise ValueError("该小章节已完成，无需重试")
        if not item.source_path:
            raise ValueError("请先为该章节指定成品文件")
        item.action = "upload"
        item.status = "retry"
        item.reason = "已排队重试；会重新上传、回读、点“下一章”"
        self._write_event("item_marked_retry", item.to_dict())
        return item

    def export_member_template(self) -> Path:
        return write_template(self.output_dir / "成员信息模板.csv")

    def load_member_sheet(self, source_path: str) -> list[dict[str, str]]:
        self.members = load_members(Path(source_path))
        self.member_sources = [str(Path(source_path).resolve())]
        self._write_event("member_preview", {"count": len(self.members)})
        return self.members

    def extract_bidder_from_project(self) -> dict[str, object]:
        if not self.project_dir:
            raise ValueError("请先选择项目和投标单位")
        root = bid_root(self.project_dir, self.bidder).resolve()
        self.bidder_profile, self.bidder_agent, source = extract_bidder_profile(root)
        self.bidder_source = str(source)
        self._write_event("bidder_pdf_preview", {"source": self.bidder_source, "profile": self.bidder_profile, "agent": self.bidder_agent})
        return self.bidder_profile

    def write_bidder_and_verify(self) -> None:
        if not self.gateway or not self.project_id:
            raise GatewayError("请先读取软件当前项目")
        if not self.bidder_profile:
            raise ValueError("请先从项目标书识别投标人资料")
        current = self.gateway.bidder_info()
        if not current.success or not isinstance(current.data, dict):
            raise GatewayError("读取软件投标人资料失败: " + current.message)
        payload = dict(current.data)
        payload.update(self.bidder_profile)
        saved = self.gateway.update_bidder(payload)
        if not saved.success:
            raise GatewayError("投标人资料保存失败: " + saved.message)
        refreshed = self.gateway.bidder_info()
        if not refreshed.success or not isinstance(refreshed.data, dict):
            raise GatewayError("投标人资料已提交，但无法重新读取核验")
        for key, expected in self.bidder_profile.items():
            if str(refreshed.data.get(key, "")) != str(expected):
                raise GatewayError(f"投标人资料保存后核验不一致：{key}")

        # 委托代理人属于项目记录；保持原项目其余字段及身份证附件不变。
        if any(self.bidder_agent.values()):
            project = self.gateway.project_info(self.project_id)
            if not project.success or not isinstance(project.data, dict):
                raise GatewayError("无法读取项目以保存委托代理人")
            project_payload = dict(project.data)
            project_payload["id"] = self.project_id
            project_payload.update({key: value for key, value in self.bidder_agent.items() if value})
            project_saved = self.gateway.save_project_draft(project_payload)
            if not project_saved.success:
                raise GatewayError("委托代理人保存失败: " + project_saved.message)
            project_readback = self.gateway.project_info(self.project_id)
            if not project_readback.success or not isinstance(project_readback.data, dict):
                raise GatewayError("委托代理人已提交，但无法重新读取核验")
            for key, expected in self.bidder_agent.items():
                if expected and str(project_readback.data.get(key, "")) != expected:
                    raise GatewayError(f"委托代理人保存后核验不一致：{key}")
        self._write_event("bidder_verified", {"profile": self.bidder_profile, "agent": self.bidder_agent})

    def extract_members_from_project(self) -> list[dict[str, str]]:
        if not self.project_dir:
            raise ValueError("请先选择项目和投标单位")
        root = bid_root(self.project_dir, self.bidder).resolve()
        self.members, sources = extract_project_members(root)
        self.member_sources = [str(path) for path in sources]
        self._write_event("member_pdf_preview", {
            "count": len(self.members), "sources": self.member_sources,
        })
        return self.members

    def write_members_and_verify(self) -> None:
        """只覆盖成员表，其余当前项目数据保持由软件读取到的值。"""
        if not self.gateway or not self.project_id:
            raise GatewayError("请先读取软件当前项目")
        if not self.members:
            raise ValueError("请先加载并校验成员资料表")
        current = self.gateway.project_info(self.project_id)
        if not current.success or not isinstance(current.data, dict):
            raise GatewayError("读取当前项目失败: " + current.message)
        payload = dict(current.data)
        payload["id"] = self.project_id
        payload["projectMemberList"] = self.members
        saved = self.gateway.save_project_draft(payload)
        if not saved.success:
            raise GatewayError("成员信息保存失败: " + saved.message)
        refreshed = self.gateway.project_info(self.project_id)
        if not refreshed.success or not isinstance(refreshed.data, dict):
            raise GatewayError("成员信息已提交，但无法重新读取核验")
        actual = refreshed.data.get("projectMemberList") or []
        if not isinstance(actual, list) or not equivalent(self.members, actual):
            raise GatewayError("成员信息保存后核验不一致，已停止；请在软件内检查")
        self._write_event("member_verified", {"count": len(self.members)})

    def verify_one(self, chapter_code: str) -> UploadItem:
        self.stop_requested = False
        item = self._item(chapter_code)
        self._upload_and_verify(item, "pilot")
        if item.status == "success":
            self.pilot_verified = True
        self.write_report("pilot")
        return item

    def advance_one(self, chapter_code: str) -> UploadItem:
        """单独执行一个无需上传附件的小章节，并做真实界面回读核验。"""
        self.stop_requested = False
        item = self._item(chapter_code)
        if item.chapter_submitted:
            raise ValueError("该小章节已经完成")
        if item.action == "upload":
            raise ValueError("附件章节请使用“验证上传 + 下一章”")
        if item.action in {"manual", "date", "boq"} and item.status != "success":
            raise ValueError("该章节的填写或前置导入尚未核验成功")
        if item.action not in {"generated", "optional_skip", "missing_skip", "user_skip", "manual", "date", "boq"}:
            raise ValueError("该章节当前不能执行下一章")
        self._advance_and_verify(item)
        self.write_report("single-next")
        return item

    def import_boq_and_verify(self, source_path: str) -> None:
        if not self.gateway or not self.project_id or not self.tbs_id or not self.project_dir:
            raise GatewayError("请先连接软件并读取项目")
        source = Path(source_path).resolve()
        if not source.is_file():
            raise ValueError("工程量清单文件不存在")
        if self.project_dir.resolve() not in source.parents:
            raise ValueError("只能选择当前项目文件夹内的工程量清单")
        allowed = {".tbfjgcl", ".tbjtgcl", ".tbslgcl", ".tbsygcl", ".tbszgcl", ".tbyllhgcl", ".xlsx", ".xls", ".pdf"}
        if source.suffix.lower() not in allowed:
            raise ValueError("不支持的工程量清单格式")
        added = self.gateway.add_file(str(source))
        if not added.success or not isinstance(added.data, dict):
            raise GatewayError("软件暂存清单失败: " + added.message)
        staged = str(added.data.get("path") or added.data.get("filePath") or "")
        raw = str(added.data.get("srcPath") or source)
        if not staged:
            raise GatewayError("软件暂存清单未返回路径")
        imported = self.gateway.import_boq(self.project_id, raw, staged)
        if not imported.success:
            raise GatewayError("工程量清单解析失败: " + imported.message)
        info = self.gateway.tbs_info(self.tbs_id)
        if not info.success or not isinstance(info.data, dict):
            raise GatewayError("清单已提交，但无法重新读取核验")
        boq = info.data.get("boq") or {}
        solid = info.data.get("solidifyBoqList") or []
        if not (isinstance(boq, dict) and boq.get("path")) and not solid:
            raise GatewayError("清单接口成功，但软件重新读取后没有清单文件")
        self.boq_verified = True
        self.boq_path = str(source)
        for item in self.items:
            if item.action == "boq":
                item.status = "success"
                item.reason = "前置工程量清单已导入并由软件读回核验"
        self._write_event("boq_verified", {"source": str(source)})

    def save_legal_date_and_verify(self, date_value: str) -> UploadItem:
        if not self.gateway or not self.project_id or not self.tbs_id:
            raise GatewayError("请先连接软件并读取项目")
        try:
            datetime.strptime(date_value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("请选择有效日期") from exc
        item = next((x for x in self.items if x.action == "date"), None)
        if not item:
            raise GatewayError("软件实际章节中未找到法定代表人身份证明书")
        nav_code = self._root_nav(item.chapter_code)
        saved = self.gateway.save_fields(self.tbs_id, nav_code, [{
            "key": "legal_rep_id_proof_date", "value": date_value, "isEnum": {"value": "0"},
        }])
        if not saved.success:
            raise GatewayError("日期写入失败: " + saved.message)
        actual = self._field_values().get("legal_rep_id_proof_date")
        if actual != date_value:
            raise GatewayError("日期已提交，但软件重新读取结果不一致")
        item.status = "success"
        item.reason = f"已写入并读回核验：{date_value}"
        self._write_event("legal_date_verified", {"date": date_value})
        return item

    def verify_manual_chapter(self, chapter_code: str) -> UploadItem:
        item = self._item(chapter_code)
        if item.action != "manual":
            raise ValueError("该章节不是人工填写章节")
        chapter = next((x for x in self.chapters if x.code == chapter_code), None)
        if not chapter:
            raise GatewayError("找不到软件章节")
        values = self._field_values()
        required: dict[str, str] = {}
        try:
            content = json.loads(chapter.raw.get("chapterContent") or "{}")
        except json.JSONDecodeError:
            content = {}
        def walk(node: Any) -> None:
            if isinstance(node, dict):
                code = str(node.get("elementCode") or "")
                try:
                    config = json.loads(node.get("configInfo") or "{}")
                except (json.JSONDecodeError, TypeError):
                    config = {}
                if code and config.get("isRequire") == 1:
                    required[code] = str(config.get("elementName") or code)
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)
        walk(content)
        missing = [name for code, name in required.items() if values.get(code) in (None, "", [])]
        if missing:
            item.status = "manual"
            item.reason = "软件内仍缺少必填项：" + "、".join(missing[:8])
            raise GatewayError(item.reason)
        item.status = "success"
        item.reason = "已重新读取软件字段，必填项均有值"
        self._write_event("manual_chapter_verified", {"chapter": chapter_code})
        return item

    def _field_values(self) -> dict[str, Any]:
        if not self.gateway:
            return {}
        reply = self.gateway.chapter_key_data(self.project_id, self.tbs_id)
        if not reply.success or not isinstance(reply.data, dict):
            raise GatewayError("无法读取软件表单字段")
        data = reply.data
        values: dict[str, Any] = {}
        for key in ("zbInfo", "bidderInfo", "entireProjectPriceKeys"):
            if isinstance(data.get(key), dict):
                values.update(data[key])
        zb_xml = data.get("zbXml") or {}
        if isinstance(zb_xml, dict):
            info = zb_xml.get("ZBInfo") or {}
            for key in ("ProjectInfo", "SubjectInfo"):
                if isinstance(info.get(key), dict):
                    values.update(info[key])
        for field in data.get("fields") or []:
            if isinstance(field, dict) and field.get("key"):
                values[str(field["key"])] = field.get("value")
        return values

    def _root_nav(self, chapter_code: str) -> str:
        by_code = {chapter.code: chapter for chapter in self.chapters}
        current = by_code.get(chapter_code)
        while current and current.parent_code and current.parent_code != "chapter-root":
            current = by_code.get(current.parent_code)
        if not current:
            raise GatewayError("无法确定章节所属大类")
        return current.code

    def run_batch(self) -> list[UploadItem]:
        if not self.pilot_verified:
            raise RuntimeError("单文件尚未核验成功，批量上传被锁定")
        prerequisites = [
            item.chapter_title for item in self.items
            if item.action in {"manual", "date", "boq"} and item.status != "success"
        ]
        missing_files = [
            item.chapter_title for item in self.items
            if item.action == "upload" and item.status in {"pending", "retry"} and not item.source_path
        ]
        if prerequisites:
            raise RuntimeError("以下前置章节尚未完成核验：" + "、".join(prerequisites))
        if missing_files:
            raise RuntimeError("以下必传章节尚未指定文件：" + "、".join(missing_files))
        self.stop_requested = False
        for item in self.items:
            if self.stop_requested:
                self._write_event("batch_stopped", {"next_chapter": item.chapter_code})
                break
            if item.chapter_submitted:
                continue
            if item.action == "upload":
                if item.status in {"pending", "retry"}:
                    self._upload_and_verify(item, "batch")
                elif item.status == "success":
                    self._advance_and_verify(item)
            elif item.action in {"generated", "optional_skip", "missing_skip", "user_skip"}:
                self._advance_and_verify(item)
            elif item.action in {"manual", "date", "boq"} and item.status == "success":
                self._advance_and_verify(item)
            self.write_report("batch")
        if not self.stop_requested:
            self._verify_completed_sections()
        return self.items

    def _verify_completed_sections(self) -> None:
        """只核验大类下的小章节；绝不再直接把大类伪造为完成。"""
        if not self.gateway:
            return
        roots = [chapter for chapter in self.chapters if chapter.parent_code == "chapter-root"]
        for root in roots:
            descendants = [item for item in self.items if self._root_nav(item.chapter_code) == root.code]
            if not descendants:
                continue
            complete = all(item.chapter_submitted for item in descendants)
            if not complete:
                pending = [item.chapter_title for item in descendants if not item.chapter_submitted]
                self._write_event("section_not_completed", {
                    "chapter": root.code, "title": root.title, "pending": pending,
                })
                continue
            self._write_event("section_children_completed", {
                "chapter": root.code, "title": root.title, "count": len(descendants),
            })

    def _upload_and_verify(self, item: UploadItem, mode: str) -> None:
        if not self.gateway:
            raise GatewayError("未连接编制工具")
        if not item.source_path:
            item.status, item.reason = "skip", "未指定文件"
            return
        chapter = next((c for c in self.chapters if c.code == item.chapter_code), None)
        if not chapter or not chapter.allow_upload or chapter.disabled_upload:
            item.status, item.reason = "failed", "章节当前不允许上传，已停止"
            return
        try:
            self._check_stopped()
            source = SourceFile(Path(item.source_path), Path(item.source_path).name, Path(item.source_path).stem)
            prepared = prepare_pdf(source, self.output_dir / "pdf")
            item.prepared_pdf = str(prepared)
            self._check_stopped()
            added = self.gateway.add_file(str(prepared))
            if not added.success or not isinstance(added.data, dict):
                raise GatewayError("软件暂存文件失败: " + added.message)
            local_link = str(added.data.get("filePath") or added.data.get("path") or "")
            if not local_link:
                raise GatewayError("软件暂存文件未返回路径")
            self._check_stopped()
            converted = self.gateway.convert_to_tool_pdf(local_link)
            if not converted.success or not isinstance(converted.data, dict):
                raise GatewayError("软件 PDF 校验/转换失败: " + converted.message)
            pdf_link = str(converted.data.get("filePath") or converted.data.get("path") or "")
            if not pdf_link:
                raise GatewayError("软件 PDF 校验未返回路径")
            self._check_stopped()
            replaced = self.gateway.replace_chapter(self.tbs_id, item.chapter_code, local_link, pdf_link)
            if not replaced.success:
                raise GatewayError("软件章节替换失败: " + replaced.message)
            verified = self._refresh_chapter(item.chapter_code)
            if not verified.existing_pdf_link:
                raise GatewayError("替换接口成功，但重新读取章节未发现 PDF 链接")
            item.verified_link = verified.existing_pdf_link
            item.verified_status = verified.replace_status
            item.reason = f"{mode}：附件已读回，正在真实界面点“下一章”"
            self._advance_and_verify(item)
            item.status = "success"
            item.reason = f"{mode}：附件读回成功，小章节已点“下一章”并读回完成状态"
        except InterruptedError as exc:
            item.status = "interrupted"
            item.reason = str(exc)
        except Exception as exc:
            item.status = "failed"
            item.reason = str(exc)
        self._write_event("upload_result", item.to_dict())

    def _advance_and_verify(self, item: UploadItem) -> None:
        if not self.gateway:
            raise GatewayError("未连接编制工具")
        if item.chapter_submitted:
            return
        self._check_stopped()
        root_code = self._root_nav(item.chapter_code)
        clicked = self.gateway.click_visible_next_chapter(
            self.project_id, root_code, item.chapter_title,
        )
        if not clicked.success:
            raise GatewayError(clicked.message)

        # 真实按钮会异步执行 fieldSave 和 setChapterSubmitStatus。
        # 只有目标小章节读回 isSubmit=1 才能计为完成。
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self._check_stopped()
            refreshed = self._refresh_chapter(item.chapter_code)
            if self._submit_value(refreshed) == "1":
                marker = self.gateway.visible_chapter_marker(item.chapter_title)
                if marker.get("done"):
                    item.chapter_submitted = True
                    item.completion_evidence = (
                        "真实界面已点“下一章”，读回 isSubmit=1，灰色勾已变绿色完成勾"
                    )
                    self._write_event("chapter_advanced", {
                        **item.to_dict(), "visibleMarker": marker,
                    })
                    return
            time.sleep(0.5)
        errors = [
            text for text in self.gateway.visible_form_errors()
            if "提交成功" not in text
        ]
        detail = "；".join(dict.fromkeys(errors)) if errors else "软件未返回完成状态"
        raise GatewayError(f"已点“下一章”，但小章节未完成：{detail}")

    @staticmethod
    def _submit_value(chapter: Chapter) -> str:
        value: Any = chapter.raw.get("isSubmit") or ""
        if isinstance(value, dict):
            value = value.get("value") or ""
        return str(value)

    def _refresh_chapter(self, chapter_code: str) -> Chapter:
        if not self.gateway:
            raise GatewayError("未连接")
        nav_reply = self.gateway.navs(self.tbs_id)
        if not nav_reply.success:
            raise GatewayError("重新读取章节导航失败: " + nav_reply.message)
        for chapter in parse_chapters(nav_reply.data):
            if chapter.code == chapter_code:
                return chapter
        for nav_code in self._nav_codes(nav_reply.data):
            reply = self.gateway.chapter_list(self.tbs_id, nav_code)
            if reply.success:
                for chapter in parse_chapters(reply.data):
                    if chapter.code == chapter_code:
                        return chapter
        raise GatewayError("重新读取章节时找不到目标章节")

    def _find_matching_project(self) -> str:
        if not self.gateway or not self.zjzbs_path:
            return ""
        reply = self.gateway.list_projects()
        if not reply.success:
            return ""
        target = self.zjzbs_path.resolve()
        records = self._records(reply.data)
        for record in records:
            path = str(record.get("filePath") or record.get("zbsPath") or "")
            try:
                if Path(path).resolve() == target:
                    return str(record.get("id") or "")
            except OSError:
                continue
        # 测试或归档目录里可能是同一份 .zjzbs 的字节级副本。
        # 软件会按文件哈希去重，因此不能只比较路径；也不能
        # 只按文件名猜测。这里仅在 SHA-256 唯一相同时接管现有记录。
        try:
            target_size = target.stat().st_size
            target_hash = self._file_sha256(target)
        except OSError:
            return ""
        matches: list[str] = []
        for record in records:
            candidate_text = str(record.get("filePath") or record.get("zbsPath") or "")
            if not candidate_text:
                continue
            candidate = Path(candidate_text)
            try:
                if candidate.is_file() and candidate.stat().st_size == target_size:
                    if self._file_sha256(candidate) == target_hash:
                        matches.append(str(record.get("id") or ""))
            except OSError:
                continue
        unique = [value for value in dict.fromkeys(matches) if value]
        return unique[0] if len(unique) == 1 else ""

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _records(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            for key in ("records", "list", "data"):
                if isinstance(value.get(key), list):
                    return [x for x in value[key] if isinstance(x, dict)]
        return []

    @staticmethod
    def _find_value(value: Any, key: str) -> str:
        if isinstance(value, dict):
            if value.get(key) is not None:
                return str(value[key])
            for child in value.values():
                found = UploadController._find_value(child, key)
                if found:
                    return found
        if isinstance(value, list):
            for child in value:
                found = UploadController._find_value(child, key)
                if found:
                    return found
        return ""

    @staticmethod
    def _nav_codes(value: Any) -> list[str]:
        found: list[str] = []
        def walk(node: Any) -> None:
            if isinstance(node, dict):
                candidate = node.get("navCode") or node.get("code")
                if candidate:
                    found.append(str(candidate))
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)
        walk(value)
        return list(dict.fromkeys(found))

    def _item(self, chapter_code: str) -> UploadItem:
        item = next((x for x in self.items if x.chapter_code == chapter_code), None)
        if not item:
            raise ValueError("请选择一个章节")
        return item

    def _check_stopped(self) -> None:
        if self.stop_requested:
            raise InterruptedError("用户已请求停止；未开始下一步上传")

    def _write_event(self, event: str, data: dict[str, Any]) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        with (self.logs_dir / f"{self.run_id}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "event": event, "data": data}, ensure_ascii=False) + "\n")

    def write_report(self, mode: str) -> Path:
        if not self.project_dir or not self.zjzbs_path:
            raise RuntimeError("尚未选择项目")
        report = RunReport(self.run_id, mode, str(self.project_dir), self.bidder, str(self.zjzbs_path), items=self.items)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        path = self.logs_dir / f"report_{self.run_id}.json"
        path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path
