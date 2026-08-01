from pathlib import Path
import tempfile
import unittest

from bid_upload_assistant.controller import UploadController
from bid_upload_assistant.models import Chapter, SourceFile


def chapter(code: str, title: str) -> Chapter:
    return Chapter(code, title, "business-root", True, False, raw={})


class PlanRuleTests(unittest.TestCase):
    def test_boq_generated_chapters_never_match_similar_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            controller = UploadController(Path(folder))
            controller.boq_verified = True
            controller.chapters = [
                chapter("cover", "投标总报价封面"),
                chapter("quote", "工程量清单报价"),
                chapter("priced", "已标明价格的工程量清单"),
            ]
            misleading = Path(folder) / "工程量清单报价说明.pdf"
            controller.sources = [SourceFile(misleading, misleading.name, misleading.stem)]

            items = controller.build_plan()

            self.assertEqual([item.action for item in items], ["boq", "boq", "boq"])
            self.assertTrue(all(item.status == "success" for item in items))
            self.assertTrue(all(not item.source_path for item in items))

    def test_boq_generated_chapters_block_until_initial_import(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            controller = UploadController(Path(folder))
            controller.chapters = [chapter("quote", "工程量清单报价")]

            item = controller.build_plan()[0]

            self.assertEqual(item.action, "boq")
            self.assertEqual(item.status, "pending")

    def test_pending_boq_cannot_be_advanced(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            controller = UploadController(Path(folder))
            controller.chapters = [chapter("quote", "工程量清单报价")]
            controller.build_plan()

            with self.assertRaisesRegex(ValueError, "前置导入尚未核验"):
                controller.advance_one("quote")


if __name__ == "__main__":
    unittest.main()
