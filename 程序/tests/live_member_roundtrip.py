"""显式运行的真实软件回归测试：写入虚构成员、核验、恢复原成员列表。"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bid_upload_assistant.gateway import CdpTenderGateway
from bid_upload_assistant.members import equivalent


TEST_MEMBER = {
    "bid_member_name": "测试验证成员",
    "bid_member_type": "1",
    "bid_member_cert_no": "11010519491231002X",
    "bid_member_certificate_name": "测试职业资格",
    "bid_member_certificate_num": "TEST-ONLY-001",
}


def save(gateway: CdpTenderGateway, project: dict, members: list[dict]) -> None:
    payload = deepcopy(project)
    payload["id"] = project["id"]
    payload["projectMemberList"] = members
    reply = gateway.save_project_draft(payload)
    if not reply.success:
        raise RuntimeError("保存失败: " + reply.message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    args = parser.parse_args()
    gateway = CdpTenderGateway()
    gateway.connect()
    original: list[dict] = []
    try:
        read = gateway.project_info(args.project_id)
        if not read.success or not isinstance(read.data, dict):
            raise RuntimeError("无法读取项目")
        project = read.data
        original = deepcopy(project.get("projectMemberList") or [])
        save(gateway, project, [TEST_MEMBER])
        after_write = gateway.project_info(args.project_id)
        actual = after_write.data.get("projectMemberList") if isinstance(after_write.data, dict) else None
        if not isinstance(actual, list) or not equivalent([TEST_MEMBER], actual):
            raise RuntimeError("写入后重新读取不一致")
        print("WRITE_AND_READBACK_OK")
        save(gateway, project, original)
        after_restore = gateway.project_info(args.project_id)
        restored = after_restore.data.get("projectMemberList") if isinstance(after_restore.data, dict) else None
        if not isinstance(restored, list) or not equivalent(original, restored):
            raise RuntimeError("恢复后重新读取不一致")
        print("RESTORE_AND_READBACK_OK")
        return 0
    finally:
        gateway.close()


if __name__ == "__main__":
    raise SystemExit(main())
