from pathlib import Path
import unittest

from bid_upload_assistant.member_extract import extract_project_members


class MemberExtractionTests(unittest.TestCase):
    def test_real_test_project_tables_are_merged(self) -> None:
        bid_root = Path(__file__).resolve().parents[2] / "测试资料" / "测试专用" / "7-29-径山镇潘板集镇有机更新项目" / "投标文件"
        if not bid_root.is_dir():
            self.skipTest("本机测试项目不存在")

        members, sources = extract_project_members(bid_root)

        self.assertEqual([member["bid_member_name"] for member in members], ["王林", "周梁", "陈高瀚", "王荣明"])
        self.assertEqual([member["bid_member_type"] for member in members], ["1", "3", "4", "9"])
        self.assertEqual(members[0]["bid_member_certificate_num"], "浙2332010202313475")
        self.assertEqual(members[2]["bid_member_certificate_num"], "浙建安C3(2019)6195930")
        self.assertEqual(len(sources), 2)


if __name__ == "__main__":
    unittest.main()
