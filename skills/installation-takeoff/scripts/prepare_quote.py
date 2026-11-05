"""Recalculate reviewed observations, never read drawings or call a model. Python 3.9+."""
import argparse
import collections
import copy
import csv
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from pathlib import Path

VERSION = "quote-app-36791a0"
LINEAR = {"countertop", "wall_cabinet", "base_cabinet", "desk"}
COUNT = {"light_strip": "条", "drawer": "个", "pull_basket": "个", "dish_basket": "个", "seasoning_basket": "个", "door": "扇"}
ACCESSORY = set(COUNT) - {"door"}
TYPES = LINEAR | set(COUNT) | {"cabinet", "wall_panel", "tatami", "custom"}
METHODS = {"projection_area": "投影面积", "plan_area": "平面面积", "developed_area": "展开面积", "length": "长度米", "count": "数量", "manual": "人工核算"}
LAYERS = {"provider": 1, "project": 2, "item": 3}
SELECTORS = {"product_type", "cabinet_subtype", "name", "space", "id"}
UNITS = {"平米", "米", "个", "条", "扇", "套", "组", "项"}
HEADERS = ["空间", "产品名称", "长度(mm)", "高度(mm)", "深度/宽度(mm)", "计价方式", "计价单位", "数量", "单价", "金额", "复核状态", "备注", "图纸缩略图", "来源/计算依据", "行编号"]


def number(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{label}须为数值")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{label}不是有效数值") from None
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise ValueError(f"{label}须为{'正' if positive else '非负'}有限数值")
    return result


def rounded(value, places=3):
    return float(value.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))


def validate_rule(rule):
    if not isinstance(rule, dict):
        raise ValueError("覆盖规则须为对象")
    if set(rule) - {"id", "version", "layer", "selector", "method", "unit", "minimum", "source_text"}:
        raise ValueError("覆盖规则包含未支持字段；复杂公式请使用明确人工核算项")
    if not isinstance(rule.get("id"), str) or not rule["id"] or not isinstance(rule.get("layer"), str) or rule["layer"] not in LAYERS or not rule.get("source_text"):
        raise ValueError("覆盖规则缺少id、有效layer或用户原话source_text")
    selector = rule.get("selector")
    if not isinstance(selector, dict) or not selector or set(selector) - SELECTORS:
        raise ValueError("覆盖规则selector为空或含未知匹配字段")
    if any(not isinstance(v, str) or not v.strip() for v in selector.values()):
        raise ValueError("selector值须为非空文本")
    if rule["layer"] == "item" and "id" not in selector:
        raise ValueError("行级覆盖必须指定id")
    method, unit = rule.get("method"), rule.get("unit")
    if not isinstance(method, str) or not isinstance(unit, str) or method not in METHODS or unit not in UNITS:
        raise ValueError("不支持的计量方式或单位；请使用明确人工核算项")
    if (method.endswith("area") and unit != "平米") or (method == "length" and unit != "米"):
        raise ValueError("规则计量方式与单位不匹配")
    if method == "count" and unit in {"米", "平米"}:
        raise ValueError("计数方式不能使用面积/长度单位")
    minimum = number(rule.get("minimum", 0), "最低计量值")
    if method == "count" and minimum % 1:
        raise ValueError("计数类最低计量值须为整数")


def default_rule(item):
    kind = item.get("product_type")
    minimum = 0
    if kind not in TYPES:
        raise ValueError("产品类型不在标准目录；需显式定义custom人工项")
    if kind == "cabinet":
        height = number(item.get("height_mm"), "柜体高度", True)
        method = "projection_area" if is_sideboard(item) or height >= 1000 else "length"
        minimum = 1 if method == "length" else 0
    elif kind in LINEAR:
        method = "length"
    elif kind in COUNT:
        method = "count"
    elif kind == "tatami":
        method = "plan_area"
    elif kind == "wall_panel":
        method = "projection_area"
    else:
        method = "manual"
    unit = "平米" if method.endswith("area") else "米" if method == "length" else COUNT.get(kind, item.get("manual_unit"))
    return dict(id=VERSION, method=method, unit=unit, minimum=minimum)


def is_sideboard(item):
    """Explicit subtype is stable across renames; old observations keep their fallback."""
    subtype = item.get("cabinet_subtype")
    if subtype is not None and (not isinstance(subtype, str) or subtype not in {"ordinary", "sideboard"}):
        raise ValueError("cabinet_subtype须为ordinary或sideboard")
    return subtype == "sideboard" if subtype is not None else "餐边柜" in item.get("name", "")


def object_list(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label}须为数组")
    for index, child in enumerate(value):
        if not isinstance(child, dict):
            raise ValueError(f"{label}[{index}]须为对象")
    return value


def repeat_value(item):
    repeat = number(item.get("repeat_count", 1), "repeat_count重复次数", True)
    if repeat % 1 or (repeat > 1 and (not isinstance(item.get("repeat_evidence"), str) or not item["repeat_evidence"].strip())):
        raise ValueError("repeat_count重复次数须为整数且有标题/用户依据")
    if repeat > 20 and not item.get("user_confirmation"):
        raise ValueError("repeat_count重复次数异常偏大，请人工核对")
    return repeat


def inherit_parent_repeat(item, by_id):
    """Return a new calculation row; the stored per-parent observation stays unchanged."""
    if item.get("product_type") not in ACCESSORY:
        return item
    parent_id = item.get("parent_id")
    parent = by_id.get(parent_id) if isinstance(parent_id, str) else None
    if not parent or parent.get("product_type") in ACCESSORY or parent["id"] == item["id"]:
        raise ValueError("配件缺少有效父柜归属")
    repeat = repeat_value(parent)
    if "repeat_count" in item and number(item["repeat_count"], "配件repeat_count", True) != repeat:
        raise ValueError("配件repeat_count与父柜重复次数冲突，须按每个父柜数量记录")
    result = dict(item, repeat_count=int(repeat), repeat_inherited_from=parent["id"])
    if repeat > 1:
        result["repeat_evidence"] = parent["repeat_evidence"]
        if parent.get("user_confirmation") and repeat > 20:
            # Preserve explicit confirmation of an unusually large parent repeat only.
            result["repeat_confirmation"] = parent["user_confirmation"]
    return result


def select_rule(item, overrides):
    matches = [r for r in overrides if all(item.get(k) == v for k, v in r["selector"].items())]
    if not matches:
        return default_rule(item)
    rank = lambda r: (LAYERS[r["layer"]], "id" in r["selector"], len(r["selector"]))
    best = max(map(rank, matches))
    winners = [r for r in matches if rank(r) == best]
    if len({(r["method"], r["unit"], r.get("minimum", 0)) for r in winners}) > 1:
        raise ValueError("同优先级计量规则冲突，请明确生效口径")
    return winners[0]


def measure(item, rule):
    for key in ("linear_segments", "components"):
        object_list(item.get(key, []), key)
    method = rule["method"]
    def dim(key):
        value = number(item.get(key), key, True)
        if value < 20 or value > 30000:
            raise ValueError(f"{key}异常，请核对毫米单位/尺寸轴")
        return value
    if method == "projection_area":
        length, height = dim("length_mm"), dim("height_mm")
        base = length * height / 1000000
        formula = f"{length}×{height}÷1000000"
        side = number(item.get("side_panel_area", 0), "连接侧板面积")
        if side:
            if not is_sideboard(item) or not item.get("side_panel_evidence"):
                raise ValueError("额外连接侧板面积缺少餐边柜适用证据")
            base += side
            formula += f"+{side}"
    elif method == "plan_area":
        length, depth = dim("length_mm"), dim("depth_mm")
        base, formula = length * depth / 1000000, f"{length}×{depth}÷1000000"
    elif method == "length":
        lengths = item.get("installation_lengths_mm")
        if item.get("product_type") in ACCESSORY:
            count = number(item.get("count"), "安装位置数", True)
            if count % 1 or not isinstance(lengths, list) or len(lengths) != int(count):
                raise ValueError("按长度计配件需逐个位置的installation_lengths_mm，不能将一条长度当总长")
            runs = [number(v, f"installation_lengths_mm[{i}]", True) for i, v in enumerate(lengths)]
            if any(v < 20 or v > 30000 for v in runs):
                raise ValueError("installation_lengths_mm异常，请核对毫米单位/尺寸轴")
            length = sum(runs, Decimal(0))
        else:
            length = dim("length_mm")
        segments = item.get("linear_segments", [])
        if segments:
            runs = []
            for s in segments:
                if not isinstance(s.get("role"), str) or s["role"] not in {"billable_run", "adjacent_depth_projection"} or not s.get("evidence"):
                    raise ValueError("长度链存在未确认段或缺少转角排除依据")
                n = number(s.get("length_mm"), "分段长度", True)
                if s["role"] == "billable_run":
                    runs.append(n)
            total = sum(runs, Decimal(0))
            if not runs or abs(total - length) > Decimal("0.5"):
                raise ValueError("正向尺寸链与计量长度不一致")
        base, formula = length / 1000, f"{length}÷1000"
    elif method == "count":
        base = number(item.get("count"), "每实例数量", True)
        if base % 1:
            raise ValueError("离散数量必须为整数")
        if base > (100 if item.get("product_type") == "light_strip" else 30 if item.get("product_type") == "door" else 20) and not item.get("user_confirmation"):
            raise ValueError("数量异常偏大，请核对是否误读尺寸或编码并人工确认")
        formula = str(base)
    elif method == "developed_area":
        parts = item.get("components", [])
        if not parts:
            raise ValueError("展开面积缺少板件尺寸、数量与计入范围")
        base, formulas = Decimal(0), []
        for p in parts:
            if not p.get("name") or not p.get("evidence"):
                raise ValueError("板件缺少名称或依据")
            l, w, n = (number(p.get(k), k, True) for k in ("length_mm", "width_mm", "count"))
            if n % 1:
                raise ValueError("板件数量须为整数")
            base += l * w * n / 1000000
            formulas.append(f"{p['name']}:{l}×{w}×{n}÷1000000")
        formula = "+".join(formulas)
    else:
        if not item.get("user_confirmation") or not item.get("manual_basis") or item.get("manual_unit") != rule["unit"]:
            raise ValueError("人工核算缺少确认、单位或计算依据")
        base, formula = number(item.get("manual_quantity"), "人工数量", True), item["manual_basis"]
    repeat = repeat_value(dict(item, user_confirmation=item.get("user_confirmation") or item.get("repeat_confirmation")))
    minimum = number(rule.get("minimum", 0), "最低计量值")
    per_instance = max(base, minimum)
    return rounded(per_instance * repeat), rounded(base * repeat, 6), f"max({formula},{minimum})×{repeat}"


def prepare(data):
    if not isinstance(data, dict):
        raise ValueError("数据须为对象")
    if data.get("schema_version") != 1 or data.get("rule_version") != VERSION:
        raise ValueError("不支持的数据或标准规则版本")
    evidence_mode = data.get("evidence_mode", "text_observations")
    if not isinstance(evidence_mode, str) or evidence_mode not in {"visual", "text_observations", "synthetic"}:
        raise ValueError("evidence_mode不支持")
    policy = data.get("evidence_policy", 1)
    if isinstance(policy, bool) or not isinstance(policy, int) or policy not in {1, 2, 3}:
        raise ValueError("evidence_policy不支持")
    overrides = object_list(data.get("rule_overrides", []), "rule_overrides")
    for rule in overrides:
        validate_rule(rule)
    if len({r["id"] for r in overrides}) != len(overrides):
        raise ValueError("覆盖规则id重复")
    expected, page_map = set(), {}
    for doc in object_list(data.get("documents", []), "documents"):
        n = number(doc.get("page_count"), "文件页数", True)
        if n % 1 or not isinstance(doc.get("file"), str) or not doc["file"]:
            raise ValueError("文件名或页数无效")
        keys = {(doc["file"], p) for p in range(1, int(n) + 1)}
        if expected & keys:
            raise ValueError("文件登记重复")
        expected |= keys
    for page in object_list(data.get("pages", []), "pages"):
        if not isinstance(page.get("file"), str) or isinstance(page.get("page"), bool) or not isinstance(page.get("page"), int) or not isinstance(page.get("page_type"), str):
            raise ValueError("pages文件名、页码或页面分类类型无效")
        key = (page.get("file"), page.get("page"))
        if key in page_map or key not in expected or page.get("page_type") not in {"product_drawing", "spatial_plan", "non_billable", "unreadable"}:
            raise ValueError("页码重复、越界或页面分类无效")
        page_map[key] = page
    missing = expected - set(page_map)
    unreadable = [p for p in page_map.values() if p["page_type"] == "unreadable"]
    page_coverage = bool(expected) and not missing and not unreadable
    items = object_list(data.get("items", []), "items")
    ids = [r.get("id") for r in items]
    if any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("每行需要唯一非空id")
    duplicates = collections.Counter((r.get("instance_key"), r.get("product_type")) for r in items
        if not r.get("excluded_reason") and isinstance(r.get("instance_key"), str) and isinstance(r.get("product_type"), str))
    by_id = {r["id"]: r for r in items}
    rows, review, excluded = [], [], []
    for item in items:
        rule = None
        try:
            # Validate nested shapes before reading any child: one bad row must not crash a batch.
            for key in ("sources", "linear_segments", "components"):
                object_list(item.get(key, []), key)
            for key in ("user_confirmation", "repeat_evidence", "repeat_confirmation", "side_panel_evidence", "manual_basis", "review_reason", "excluded_reason", "remark"):
                if item.get(key) is not None and not isinstance(item[key], str):
                    raise ValueError(f"{key}须保留文本原话或依据")
            if item.get("excluded_reason"):
                excluded.append(dict(item, status="不单列", reason=item["excluded_reason"]))
                continue
            if any(not isinstance(item.get(k), str) or not item[k] for k in ("instance_key", "name", "product_type")) or item["product_type"] not in TYPES:
                raise ValueError("缺少实体键/产品名称或不支持产品类型")
            if duplicates[(item["instance_key"], item["product_type"])] > 1:
                raise ValueError("同一实体出现重复行，请先完成多视图去重")
            if policy == 3 and item["product_type"] == "cabinet":
                if not isinstance(item.get("cabinet_subtype"), str) or item["cabinet_subtype"] not in {"ordinary", "sideboard"}:
                    raise ValueError("cabinet须显式填写cabinet_subtype：ordinary或sideboard")
                if any(label in item["name"] for label in ("厨房吊柜", "厨房地柜")):
                    raise ValueError("cabinet分类与厨房吊柜/地柜名称矛盾，请核对product_type")
            item = inherit_parent_repeat(item, by_id)
            rule = select_rule(item, overrides)
            if item.get("needs_review"):
                raise ValueError(item.get("review_reason") or "尚未完成图纸复核")
            confidence = number(item.get("confidence"), "识别置信度")
            if confidence > 1 or (confidence < Decimal("0.55") and not item.get("user_confirmation")):
                raise ValueError("置信度未达到自动纳入条件或数值越界")
            sources = item.get("sources", [])
            if not sources and not (item.get("product_type") == "custom" and item.get("user_confirmation")):
                raise ValueError("缺少可追溯来源")
            measurement_sources = 0
            for index, s in enumerate(sources):
                if not isinstance(s.get("file"), str) or isinstance(s.get("page"), bool) or not isinstance(s.get("page"), int):
                    raise ValueError(f"sources[{index}]文件名或页码无效")
                p = page_map.get((s.get("file"), s.get("page")))
                role = s.get("role", "measurement")
                if not p or not isinstance(role, str) or role not in {"measurement", "context"} or any(not isinstance(s.get(k), str) or not s[k].strip() for k in ("region", "text")):
                    raise ValueError(f"sources[{index}]未核验或缺少有效role/区域/原文依据")
                if role == "measurement":
                    if p["page_type"] != "product_drawing":
                        raise ValueError(f"sources[{index}]非产品页，只可作为role=context来源")
                    measurement_sources += 1
            if not measurement_sources and not (item.get("product_type") == "custom" and item.get("user_confirmation")):
                raise ValueError("缺少产品取量来源，context来源不能作为计量依据")
            warnings = []
            if policy >= 2:
                from evidence_checks import check_evidence
                warnings = check_evidence(item, rule, number, policy=policy) or []
            quantity, measured, formula = measure(item, rule)
            if quantity <= 0:
                raise ValueError("舍入后数量为0，需核对精度")
            price, price_issue = None, ""
            if item.get("unit_price") is not None:
                try:
                    if item.get("price_unit") != rule["unit"] or not isinstance(item.get("price_note"), str) or not item["price_note"].strip():
                        raise ValueError("价格单位变化或缺少用户报价依据，请重新补价")
                    price = number(item["unit_price"], "单价")
                except ValueError as error:
                    price_issue = str(error)
            amount = rounded(Decimal(str(quantity)) * price, 2) if price is not None else None
            row = dict(item, quantity=quantity, measured_quantity=measured, method=rule["method"], unit=rule["unit"],
                             rule_id=rule["id"], formula=formula, unit_price=float(price) if price is not None else None,
                             amount=amount, price_issue=price_issue, status="人工确认" if item.get("user_confirmation") or any(w.get("resolution") == "user_confirmed" for w in warnings) else "依据齐全")
            if warnings:
                row["evidence_warnings"], row["unverified_fields"] = warnings, {}
                for warning in warnings:
                    field = warning["field"]
                    if field in {"length_mm", "height_mm", "depth_mm"} and warning.get("resolution") != "user_confirmed":
                        row["unverified_fields"][field] = row.get(field)
                        row[field] = None
            rows.append(row)
        except ValueError as error:
            reason = item.get("review_reason") if item.get("needs_review") and item.get("review_reason") else str(error)
            review.append(dict(item, quantity=None, amount=None, status="待复核", reason=reason,
                               method=rule["method"] if rule else None, unit=rule["unit"] if rule else None,
                               rule_id=rule["id"] if rule else None))
    priced = [r for r in rows if r["amount"] is not None]
    subtotal = rounded(sum((Decimal(str(r["amount"])) for r in priced), Decimal(0)), 2) if priced else None
    groups = {}
    for r in rows:
        key = (r["product_type"], r["method"], r["unit"], r["unit_price"], r.get("cabinet_subtype"))
        if key not in groups:
            groups[key] = dict(product_type=key[0], method=key[1], unit=key[2], unit_price=key[3], cabinet_subtype=key[4], quantity=Decimal(0), amount=Decimal(0) if key[3] is not None else None)
        g = groups[key]
        g["quantity"] += Decimal(str(r["quantity"]))
        if g["amount"] is not None:
            g["amount"] += Decimal(str(r["amount"]))
    summary = [{k: float(v) if isinstance(v, Decimal) else v for k, v in g.items()} for g in groups.values()]
    from evidence_checks import check_coverage
    coverage_info = check_coverage(page_map, items, policy)
    coverage = page_coverage and (policy < 3 or (coverage_info["product_coverage_complete"] and coverage_info["accessory_coverage_complete"]))
    pending_inputs = object_list(data.get("pending_review_inputs", []), "pending_review_inputs")
    has_pending = any(entry.get("status") == "pending" for entry in pending_inputs)
    result = dict(project=data.get("project", ""), rule_version=VERSION, evidence_policy=policy, rule_overrides=overrides, changes=data.get("changes", []),
                project_id=data.get("project_id"), source_fingerprint=data.get("source_fingerprint"), revision=data.get("revision"),
                pending_review_inputs=copy.deepcopy(pending_inputs), all_items=copy.deepcopy(items),
                model_info=data.get("model_info", {}), documents=data.get("documents", []), pages=data.get("pages", []),
                evidence_mode=evidence_mode, visual_verified=evidence_mode == "visual",
                coverage_complete=coverage, page_coverage_complete=page_coverage, **coverage_info,
                missing_pages=[dict(file=f, page=p) for f, p in sorted(missing)],
                unreadable_pages=unreadable, rows=rows, review=review, excluded=excluded, summary=summary,
                priced_subtotal=subtotal, complete_total=subtotal if policy == 3 and evidence_mode == "visual" and coverage and not review and not has_pending and rows and len(priced) == len(rows) else None)

    from quotation_view import quotation_view
    result.update(quotation_view(result))
    return result


def safe_csv(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def detail_values(row):
    source = "；".join(f"{s.get('file', '')} 第{s.get('page', '')}页 {s.get('region', '')}：{s.get('text', '')}" for s in (row.get("sources") if isinstance(row.get("sources"),list) else []) if isinstance(s, dict))
    notes = "；".join(str(row.get(k, "")) for k in ["remark", "reason", "price_issue", "user_confirmation"] if row.get(k))
    return [row.get("space"), row.get("name"), row.get("length_mm"), row.get("height_mm"), row.get("depth_mm"),
            METHODS.get(row.get("method")), row.get("unit"), row.get("quantity"), row.get("unit_price"), row.get("amount"),
            row.get("status"), notes, row.get("thumbnail"), source + "；" + row.get("formula", ""), row.get("id")]


def write_outputs(result, target):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=False)
    (target / "calculated.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tables = [("报价明细.csv", HEADERS, [detail_values(r) for r in result["quote_rows"]]),
              ("不单列项.csv", HEADERS, [detail_values(r) for r in result["excluded"]]),
              ("报价汇总.csv", ["产品范围", "计价方式", "计价单位", "单价", "数量", "金额"],
               [[r["product_type"], METHODS.get(r["method"], "待明确"), r["unit"], r["unit_price"], r["quantity"], r["amount"]] for r in result["quote_summary"]])]
    for name, header, rows in tables:
        with (target / name).open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows([[safe_csv(c) for c in row] for row in rows])


def main():
    parser = argparse.ArgumentParser(description="核验结构化识图结果并输出算量CSV/JSON（不读取图纸，不生成带图Excel）")
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="尚不存在的新输出目录")
    args = parser.parse_args()
    try:
        result = prepare(json.loads(args.input.read_text(encoding="utf-8-sig")))
        write_outputs(result, args.out)
    except (ValueError, OSError, TypeError, KeyError) as error:
        parser.exit(2, f"无法生成：{error}\n")
    print(f"可计算项 {len(result['rows'])}，待复核 {len(result['review'])}，页清单完整：{result['coverage_complete']}，原图已核验：{result['visual_verified']}。已保存至 {args.out}")


if __name__ == "__main__":
    main()
