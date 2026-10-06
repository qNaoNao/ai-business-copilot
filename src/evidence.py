"""Conservative rule checks, not a general-purpose factuality/causality judge."""
import math
import re
from typing import Any


def verify_answer(answer: str, knowledge: list[dict], trace: list[dict]) -> list[dict[str, Any]]:
    checks = []
    supplied = {k["citation"] for k in knowledge}
    for call in trace:
        if call["name"] == "search_business_knowledge" and call["output"].get("ok"):
            supplied.update(row["citation"] for row in call["output"]["data"] if "citation" in row)
    cited = set(re.findall(r"KB:[\w-]+:\d+@[a-f0-9]{12}", answer))
    checks.append({"name": "citation_integrity", "passed": not (cited - supplied) and (bool(cited) or not supplied),
                   "detail": "引用必须来自本轮检索证据，且检索有结果时需要引用。"})
    positive_currency = re.search(r"\d[\d,.]*\s*(?:元|人民币|美元|BRL|USD|CNY)|[￥¥$]\s*\d", answer)
    checks.append({"name": "currency_boundary", "passed": positive_currency is None,
                   "detail": "经营金额未提供币种；API 成本另行记录，不属于经营金额。"})
    assertions = []
    for line in answer.splitlines():
        if re.search(r"(?:证明|确认|确定|就是|导致).{0,10}(?:缺货|下架|流量下降|毛利下降)", line):
            if not re.search(r"不能|不代表|并不|无法|假设|待验证|可能|需要", line):
                assertions.append(line[:120])
    checks.append({"name": "causal_boundary_patterns", "passed": not assertions,
                   "detail": "仅检测部分高风险因果表达，不保证识别所有不当推断。", "flags": assertions})
    # Numeric presence is an advisory check, NOT proof that each metric is mapped correctly.
    evidence_numbers = []
    def collect(value):
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)) and math.isfinite(value):
            evidence_numbers.extend([float(value), float(value) * 100])
        elif isinstance(value, dict):
            for item in value.values(): collect(item)
        elif isinstance(value, list):
            for item in value: collect(item)
    for call in trace:
        if call["name"] != "search_business_knowledge" and call["output"].get("ok"):
            collect(call["output"]["data"])
    cleaned = re.sub(r"KB:[\w-]+:\d+@[a-f0-9]{12}|\b\d{4}-\d{2}(?:-\d{2})?\b|\b[a-f0-9]{32}\b", "", answer)
    unknown = sorted({match.replace(",", "") for match in re.findall(r"(?<![\w])[-+]?\d[\d,]*\.\d+", cleaned)
                      if evidence_numbers and not any(abs(float(match.replace(',', '')) - n) <= max(.011, abs(n) * .0001)
                                                     for n in evidence_numbers)})
    checks.append({"name": "numeric_presence_advisory", "passed": not unknown, "severity": "advisory",
                   "detail": "仅匹配数字是否出现在工具证据中；派生计算和语义仍需人工核验。", "flags": unknown})
    return checks
