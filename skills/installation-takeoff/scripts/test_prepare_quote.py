import copy
import importlib.util
import pathlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("prepare_quote", pathlib.Path(__file__).with_name("prepare_quote.py"))
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)


def item(**kw):
    value = dict(id="Q1", instance_key="C1", product_type="cabinet", name="衣柜", space="主卧",
                 length_mm=2400, height_mm=2600, depth_mm=600, confidence=0.9,
                 sources=[dict(file="synthetic.pdf", page=1, region="A立面", text="2400×2600")])
    value.update(kw)
    return value


def data(*items, **kw):
    value = dict(schema_version=1, project="合成测试", rule_version="quote-app-36791a0", evidence_mode="visual",
                 documents=[dict(file="synthetic.pdf", page_count=1)],
                 pages=[dict(file="synthetic.pdf", page=1, page_type="product_drawing")],
                 items=list(items), rule_overrides=[])
    value.update(kw)
    return value


class TakeoffTest(unittest.TestCase):
    def test_area_and_blank_price(self):
        r = q.prepare(data(item()))
        self.assertEqual(r["rows"][0]["quantity"], 6.24)
        self.assertIsNone(r["rows"][0]["amount"])
        self.assertIsNone(r["complete_total"])

    def test_low_cabinet_and_sideboard(self):
        self.assertEqual(q.prepare(data(item(length_mm=800, height_mm=900)))["rows"][0]["quantity"], 1)
        r = q.prepare(data(item(name="餐边柜", length_mm=3674, height_mm=2384)))["rows"][0]
        self.assertEqual((r["quantity"], r["unit"]), (8.759, "平米"))

    def test_tatami_and_repeat(self):
        r = q.prepare(data(item(product_type="tatami", length_mm=1955, depth_mm=1210, height_mm=1090)))["rows"][0]
        self.assertEqual(r["quantity"], 2.366)
        r = q.prepare(data(item(product_type="desk", name="会议桌", length_mm=5500,
                                repeat_count=2, repeat_evidence="会议桌×2")))["rows"][0]
        self.assertEqual(r["quantity"], 11)

    def test_missing_dimension_and_evidence(self):
        for patch in [dict(height_mm=None), dict(sources=[]), dict(confidence=.4), dict(length_mm=-10), dict(length_mm="2.4m")]:
            r = q.prepare(data(item(**patch)))
            self.assertEqual(len(r["rows"]), 0)
            self.assertEqual(len(r["review"]), 1)

    def test_zero_price_needs_explicit_source(self):
        r = q.prepare(data(item(unit_price=0, price_unit="平米", price_note="本单免费")))
        self.assertEqual(r["priced_subtotal"], 0)
        self.assertIsNone(r["complete_total"])  # Legacy evidence cannot claim v3 coverage.
        self.assertIsNone(q.prepare(data(item(unit_price=0)))["rows"][0]["unit_price"])

    def test_partial_price_total_and_coverage(self):
        a = item(unit_price=35, price_unit="平米", price_note="衣柜每平米35元")
        b = item(id="Q2", instance_key="C2")
        r = q.prepare(data(a, b))
        self.assertEqual(r["priced_subtotal"], 218.4)
        self.assertIsNone(r["complete_total"])
        self.assertFalse(q.prepare(data(a, pages=[]))["coverage_complete"])

    def test_custom_rule_and_changed_unit_price(self):
        rule = dict(id="R1", layer="project", selector={"name": "衣柜"}, method="length", unit="米", minimum=0, source_text="本单衣柜按米")
        r = q.prepare(data(item(unit_price=35, price_unit="平米", price_note="每平米35"), rule_overrides=[rule]))
        self.assertEqual(r["rows"][0]["quantity"], 2.4)
        self.assertIsNone(r["rows"][0]["unit_price"])

    def test_multiple_lights_length_override_requires_all_runs(self):
        rule = dict(id="R1", layer="project", selector={"product_type": "light_strip"}, method="length", unit="米", source_text="本单灯带按米")
        light = item(id="Q2", instance_key="lights", parent_id="Q1", product_type="light_strip", name="灯带", count=6, length_mm=500)
        r = q.prepare(data(item(), light, rule_overrides=[rule]))
        self.assertEqual(len(r["review"]), 1)
        self.assertEqual(r["review"][0]["unit"], "米")
        light["installation_lengths_mm"] = [500] * 6
        self.assertEqual(q.prepare(data(item(), light, rule_overrides=[rule]))["rows"][1]["quantity"], 3)

    def test_text_observations_are_not_visual_verification(self):
        r = q.prepare(data(item(unit_price=0, price_unit="平米", price_note="免费"), evidence_mode="text_observations"))
        self.assertTrue(r["coverage_complete"])
        self.assertFalse(r["visual_verified"])
        self.assertIsNone(r["complete_total"])

    def test_developed_area_needs_components(self):
        rule = dict(id="R1", layer="project", selector={"product_type": "cabinet"}, method="developed_area", unit="平米", source_text="衣柜展开面积")
        self.assertTrue(q.prepare(data(item(), rule_overrides=[rule]))["review"])
        r = q.prepare(data(item(components=[dict(name="侧板", length_mm=2600, width_mm=600, count=2, evidence="板件清单")]), rule_overrides=[rule]))
        self.assertEqual(r["rows"][0]["quantity"], 3.12)

    def test_conflicting_rule_and_invalid_override(self):
        rule = dict(id="R1", layer="project", selector={"name": "衣柜"}, method="length", unit="米", source_text="按米")
        other = dict(rule, id="R2", method="count", unit="组")
        self.assertTrue(q.prepare(data(item(), rule_overrides=[rule, other]))["review"])
        with self.assertRaises(ValueError):
            q.prepare(data(item(), rule_overrides=[dict(rule, method="eval('1')")]))
        with self.assertRaises(ValueError):
            q.prepare(data(item(), rule_overrides=[dict(rule, formula="length - 600")]))

    def test_duplicate_and_parent(self):
        r = q.prepare(data(item(), item(id="Q2")))
        self.assertEqual(len(r["rows"]), 0)
        r = q.prepare(data(item(product_type="drawer", count=7)))
        self.assertTrue(r["review"])

    def test_kitchen_segment_roles(self):
        seg = [dict(length_mm=1625, role="billable_run", evidence="正向链"),
               dict(length_mm=600, role="adjacent_depth_projection", evidence="相邻B面深度")]
        r = q.prepare(data(item(product_type="base_cabinet", length_mm=1625, linear_segments=seg)))
        self.assertEqual(r["rows"][0]["quantity"], 1.625)
        seg[1]["role"] = "uncertain"
        self.assertTrue(q.prepare(data(item(product_type="base_cabinet", linear_segments=seg)))["review"])

    def test_non_product_page_not_billable(self):
        r = q.prepare(data(item(), pages=[dict(file="synthetic.pdf", page=1, page_type="spatial_plan")]))
        self.assertTrue(r["review"])

    def test_output_new_directory_and_formula_text(self):
        self.assertEqual(q.safe_csv("=HYPERLINK(\"x\")")[0], "'")
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "new"
            q.write_outputs(q.prepare(data(item())), target)
            self.assertTrue((target / "报价明细.csv").exists())
            with self.assertRaises(FileExistsError):
                q.write_outputs(q.prepare(data(item())), target)


if __name__ == "__main__":
    unittest.main()
