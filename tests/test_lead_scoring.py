#!/usr/bin/env python3
"""评分引擎测试:纯规则,不依赖 LLM,跑 lead_cases.json 黄金样本 + 边界用例。"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import leadgen

CASES = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lead_cases.json"), encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_lead_cases(case):
    score, level = leadgen.score_lead(case["fields"])
    assert score == case["expected"], f"{case['name']}: 期望 {case['expected']} 分,实际 {score} 分"
    assert level == case["level"], f"{case['name']}: 期望 {case['level']},实际 {level}"


def test_score_components():
    fields = {"budget": "15万", "area": "89", "room_type": "三房",
              "start_time": "10月", "contact": "微信xxx", "raw_text": "尽快"}
    score, level = leadgen.score_lead(fields)
    assert score == 100
    assert level == "高"


def test_score_budget_only():
    score, level = leadgen.score_lead({"budget": "20万"})
    assert score == leadgen.SCORE_BUDGET
    assert level == "低"


def test_score_area_or_room_counted_once():
    with_area = leadgen.score_lead({"area": "89"})[0]
    with_room = leadgen.score_lead({"room_type": "三房"})[0]
    both = leadgen.score_lead({"area": "89", "room_type": "三房"})[0]
    assert with_area == with_room == both == leadgen.SCORE_AREA_ROOM


def test_score_never_exceeds_100():
    score, _ = leadgen.score_lead({"budget": "1", "area": "1", "room_type": "1",
                                   "start_time": "1", "contact": "1", "urgent": True})
    assert score == 100


def test_urgent_word_detection():
    assert leadgen.has_urgent("想尽快开工")
    assert leadgen.has_urgent("有点着急")
    assert leadgen.has_urgent("价格合适就定")
    assert not leadgen.has_urgent("大概了解一下")


def test_extract_json_fallback():
    """LLM 返回带前后缀的 json 时,能正确解析。"""
    fake = '好的，结果如下{"area": "89", "budget": "15万"} 以上是抽取结果'
    start, end = fake.find("{"), fake.rfind("}")
    out = json.loads(fake[start:end + 1])
    assert out["area"] == "89"


def test_normalize_extract_defaults():
    out = leadgen.normalize_extract({})
    assert out["area"] == "" and out["contact"] == ""
    assert out["is_lead"] is True
    assert out["urgent"] is False
