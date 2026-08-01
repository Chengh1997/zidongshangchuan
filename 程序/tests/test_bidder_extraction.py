from pathlib import Path
import unittest

from bid_upload_assistant.bidder_extract import extract_bidder_profile


class BidderExtractionTests(unittest.TestCase):
    def test_real_basic_information_table(self) -> None:
        bid_root = Path(__file__).resolve().parents[2] / "测试资料" / "测试专用" / "7-29-径山镇潘板集镇有机更新项目" / "投标文件"
        if not bid_root.is_dir():
            self.skipTest("本机测试项目不存在")

        profile, agent, source = extract_bidder_profile(bid_root)

        self.assertEqual(profile["name"], "浙江吉鑫环境工程有限公司")
        self.assertEqual(profile["uscc"], "91330100MA28XQ0U4K")
        self.assertEqual(profile["legalPersonName"], "王荣明")
        self.assertEqual(profile["legalPersonIdCard"], "339005198511215111")
        self.assertEqual(profile["legalPersonSex"], "男")
        self.assertEqual(profile["postalCode"], "311241")
        self.assertEqual(profile["phone"], "18058118868")
        self.assertEqual(agent["agentPerson"], "陈高瀚")
        self.assertEqual(agent["agentPersonIdCard"], "339005199706216215")
        self.assertEqual(agent["agentPhone"], "18267158260")
        self.assertEqual(source.name, "1-投标人基本情况表.pdf")


if __name__ == "__main__":
    unittest.main()
