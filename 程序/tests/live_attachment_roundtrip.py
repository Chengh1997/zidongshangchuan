"""真实软件附件上传→回读→恢复测试；所有参数必须显式给出。"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bid_upload_assistant.controller import UploadController
from bid_upload_assistant.files import PROJECT_SELF
from bid_upload_assistant.gateway import parse_chapters
from bid_upload_assistant.models import UploadItem


def snapshot(controller: UploadController, nav_code: str) -> dict[str, tuple[object, ...]]:
    assert controller.gateway
    reply = controller.gateway.navs(controller.tbs_id)
    if not reply.success:
        raise RuntimeError("读取章节树失败: " + reply.message)
    all_chapters = parse_chapters(reply.data)
    by_code = {chapter.code: chapter for chapter in all_chapters}
    wanted = {nav_code}
    changed = True
    while changed:
        changed = False
        for chapter in all_chapters:
            if chapter.parent_code in wanted and chapter.code not in wanted:
                wanted.add(chapter.code)
                changed = True
    def clean_content(raw: object) -> object:
        try:
            content = __import__("json").loads(raw or "{}")
        except Exception:
            return raw
        # 软件清空附件后会保留 link/pdfLink 的空键；这与初始“没有该键”
        # 的界面语义相同，比较时应视为同一状态。
        if isinstance(content, dict):
            for key in ("link", "pdfLink"):
                if not content.get(key):
                    content.pop(key, None)
        return content
    return {
        code: (
            by_code[code].raw.get("link") or "", by_code[code].raw.get("pdfLink") or "",
            clean_content(by_code[code].raw.get("chapterContent")), by_code[code].raw.get("replaceStatus"),
        ) for code in wanted
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--zjzbs", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--tbs-id", required=True)
    parser.add_argument("--nav-code", required=True)
    parser.add_argument("--chapter-code", required=True)
    parser.add_argument("--source-pdf", type=Path, required=True)
    args = parser.parse_args()

    controller = UploadController(args.workspace.resolve())
    controller.set_input(str(args.project_dir), PROJECT_SELF, str(args.zjzbs))
    controller.connect("http://127.0.0.1:9222")
    controller.project_id = args.project_id
    controller.tbs_id = args.tbs_id
    assert controller.gateway
    controller.chapters = parse_chapters(controller.gateway.navs(controller.tbs_id).data)
    target = next((c for c in controller.chapters if c.code == args.chapter_code), None)
    if not target or not target.allow_upload or target.disabled_upload:
        raise RuntimeError("目标章节不是当前软件允许上传的章节")
    if target.existing_pdf_link:
        raise RuntimeError("目标章节已有附件，拒绝覆盖测试")

    before = snapshot(controller, args.nav_code)
    controller.items = [UploadItem(target.code, target.title, str(args.source_pdf.resolve()), confidence="manual", reason="真实端到端测试")]
    uploaded = False
    try:
        item = controller.verify_one(target.code)
        if item.status != "success":
            raise RuntimeError("上传或回读验证失败: " + item.reason)
        uploaded = True
        after_upload = controller._refresh_chapter(target.code)
        if not after_upload.existing_pdf_link:
            raise RuntimeError("上传后章节未出现 PDF 链接")
        print("UPLOAD_AND_READBACK_OK", after_upload.existing_pdf_link)
    finally:
        # 即使回读环节报错，只要目标状态被改变也必须立即调用软件原生恢复。
        current = snapshot(controller, args.nav_code)
        if uploaded or current != before:
            restore = controller.gateway.replace_chapter(controller.tbs_id, args.chapter_code, "", "")
            if not restore.success:
                raise RuntimeError("软件清空测试附件失败，测试附件可能仍在: " + restore.message)
            for _ in range(10):
                time.sleep(0.5)
                if snapshot(controller, args.nav_code) == before:
                    print("RESTORE_AND_READBACK_OK")
                    break
            else:
                raise RuntimeError("恢复后章节内容与测试前不一致，已停止")
    controller.gateway.close()


if __name__ == "__main__":
    main()
