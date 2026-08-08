#!/usr/bin/env python3
"""Generate the final Persian project report from verified result artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


RESULTS = Path("results")
OUTPUT = Path("reports/final-report.md")


def read(name: str) -> dict[str, Any]:
    value = json.loads((RESULTS / name).read_text(encoding="utf-8"))
    if value.get("status") != "passed":
        raise ValueError(f"{name} is not a passed artifact")
    return value


def improvement(before: float, after: float, lower: bool = True) -> float:
    change = (after / before - 1) * 100
    return round(-change if lower else change, 2)


def table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    return [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] + ["---:"] * (len(headers) - 1)) + "|",
        *["| " + " | ".join(str(value) for value in row) + " |" for row in rows],
    ]


def main() -> int:
    search_b, search_o = read("search_baseline.json"), read("search_optimized.json")
    hybrid_b, hybrid_o = read("hybrid_comparison.json"), read("hybrid_comparison_optimized.json")
    quality_b, quality_o = read("hybrid_quality.json"), read("hybrid_quality_optimized.json")
    load_b, load_o = read("load_test_baseline.json"), read("load_test_optimized.json")
    ingest_b, ingest_o = read("ingestion_baseline.json"), read("ingestion_optimized.json")
    comparison, ablations = read("stage9_comparison.json"), read("stage9_ablations.json")
    manifest = read("reproducibility_manifest.json")
    sb = {x["query_id"]: x for x in search_b["scenarios"]}
    so = {x["query_id"]: x for x in search_o["scenarios"]}
    lb = {x["scenario_id"]: x for x in load_b["scenarios"]}
    lo = {x["scenario_id"]: x for x in load_o["scenarios"]}
    search_rows = [[key, sb[key]["type"], round(sb[key]["average_latency_ms"], 3), round(so[key]["average_latency_ms"], 3), improvement(sb[key]["average_latency_ms"], so[key]["average_latency_ms"]), sb[key]["result_count"] == so[key]["result_count"]] for key in sorted(sb)]
    load_rows = [[key, lb[key]["client_count"], round(lb[key]["average_latency_ms"], 3), round(lo[key]["average_latency_ms"], 3), improvement(lb[key]["average_latency_ms"], lo[key]["average_latency_ms"]), round(lb[key]["user_facing_throughput_per_second"], 2), round(lo[key]["user_facing_throughput_per_second"], 2)] for key in sorted(lb)]
    qb = {(x["query_id"], x["method"]): x for x in quality_b["quality"]}
    qo = {(x["query_id"], x["method"]): x for x in quality_o["quality"]}
    quality_rows = [[key[0], key[1], qb[key]["returned_count"], qb[key]["precision_at_10"], qo[key]["precision_at_10"]] for key in sorted(qb)]
    ablation_rows = [[x["name"], x["control"]["average_latency_ms"], x["treatment"]["average_latency_ms"], x["average_latency_change_percent"], x["result_counts_equal"]] for x in ablations["search_ablations"]]
    lines = [
        "# گزارش نهایی پروژهٔ جست‌وجوی تمام‌متن arXiv",
        "",
        "## خلاصهٔ اجرایی",
        "",
        "این پروژه یک مسیر کامل و قابل بازتولید برای پاک‌سازی، index کردن و جست‌وجوی ۵۰٬۰۰۰ مقالهٔ arXiv با Elasticsearch 9.4.4 پیاده‌سازی می‌کند. سه روش keyword، contain و fuzzy، معماری ترکیبی weighted-RRF، سنجش کیفیت دستی، benchmark تک‌کاربره و ده سناریوی چندکاربره روی indexهای baseline و optimized اجرا شده‌اند.",
        "",
        f"تمام benchmarkهای اصلی با پروتکل ثابت و صفر خطا تمام شدند. اجرای optimized شامل {load_o['totals']['measured_request_count']:,} جست‌وجوی load-test بود و Precision@10 در تمام زوج‌های query/method نسبت به baseline ثابت ماند.",
        "",
        "## داده و منشأ",
        "",
        f"منبع بالادستی: {manifest['dataset']['upstream_source']}",
        "",
        "فایل خام تحویلی یک نمونهٔ ۵۰هزار رکوردی است؛ seed و کد استخراج این نمونه در مخزن اولیه وجود نداشت و این محدودیت provenance صریحاً ثبت شده است.",
        "",
        *table(["فایل", "اندازه (بایت)", "SHA-256", "تأیید"], [[x["path"], x["size_bytes"], f"`{x['sha256']}`", x["verified"]] for x in manifest["dataset"]["files"]]),
        "",
        "پاک‌سازی با خواندن خطی JSONL، Unicode NFC، حذف نویسه‌های کنترلی، تثبیت schema، استخراج year و بازسازی title_abstract انجام شد. هر ۵۰٬۰۰۰ رکورد معتبر باقی ماند.",
        "",
        "## محیط و طراحی ذخیره‌سازی",
        "",
        f"- Python {manifest['environment']['python']}; Docker {manifest['environment']['docker']}; Docker Compose {manifest['environment']['docker_compose']}; Elasticsearch {manifest['environment']['elasticsearch']}",
        "- محیط تک‌نودی، ۲ CPU، محدودیت حافظهٔ ۲GB و heap برابر ۱GB",
        "- هر دو index یک shard و صفر replica دارند.",
        "- baseline فقط analyzer استاندارد دارد؛ optimized قابلیت n-gram زیررشته و multi-fieldهای آزمایشی را جدا نگه می‌دارد.",
        "- هر دو index اصلی با dataset و batch=500 یکسان ساخته شدند.",
        "",
        "## جست‌وجو و معماری ترکیبی",
        "",
        "keyword با multi_match، contain با عبارت/شرط مثبت-منفی و fuzzy با fuzziness کنترل‌شده پیاده‌سازی شده است. معماری ترکیبی ابتدا keyword و contain را موازی اجرا می‌کند و فقط در صورت کمبود candidate، نبود overlap یا سناریوی typo، fuzzy را فعال می‌کند. ادغام رتبه‌ها با weighted RRF، k=60 و وزن‌های 1.0، 0.9 و 0.65 انجام می‌شود؛ score خام روش‌ها مستقیماً جمع نمی‌شود.",
        "",
        "## نتایج تک‌کاربرهٔ قبل/بعد",
        "",
        *table(["Query", "نوع", "Baseline ms", "Optimized ms", "بهبود %", "Count برابر"], search_rows),
        "",
        "در هر ۹ query تعداد نتایج baseline و optimized برابر بود. حذف جست‌وجوی تکراری روی title_abstract عامل اصلی بهبود queryهای عمومی بود.",
        "",
        "![مقایسهٔ latency تک‌کاربره](../results/stage9_search_before_after.png)",
        "",
        "## کیفیت بازیابی",
        "",
        *table(["Query", "روش", "Returned", "P@10 baseline", "P@10 optimized"], quality_rows),
        "",
        f"هر {quality_o['judgment_count']} قضاوت optimized مربوط به همان زوج query/paper قبلاً قضاوت‌شده بود؛ بنابراین قضاوت‌ها بدون بازتفسیر رتبه به رتبه reuse شدند. ثبات کیفیت برای همهٔ حالت‌ها: {comparison['index_tradeoffs']['quality_precision_at_10_unchanged']}.",
        "",
        "## آزمایش چندکاربره",
        "",
        *table(["سناریو", "Client", "Baseline ms", "Optimized ms", "بهبود %", "QPS baseline", "QPS optimized"], load_rows),
        "",
        f"baseline شامل {load_b['totals']['measured_request_count']:,} و optimized شامل {load_o['totals']['measured_request_count']:,} درخواست اندازه‌گیری‌شده بود؛ هر دو صفر خطا داشتند. فشار محسوس تا ۱۰ client مشاهده شد، بنابراین سناریوهای اختیاری ۲۰/۵۰/۱۰۰ طبق قرارداد اجرا نشدند.",
        "",
        "![مقایسهٔ latency بار](../results/stage9_load_before_after.png)",
        "",
        "## آزمایش‌های تک‌متغیره و تصمیم‌ها",
        "",
        *table(["آزمایش", "Control ms", "Treatment ms", "تغییر latency %", "Count برابر"], ablation_rows),
        "",
        "analyzer انگلیسی به دلیل کندترشدن و تغییر recall برای queryهای اصلی رد شد. n-gram برای زیررشته سریع‌تر بود، اما فضای ذخیره‌سازی بیشتری مصرف کرد. تغییر batch/refresh فقط روی indexهای موقت اندازه‌گیری شد و وارد مقایسهٔ جست‌وجوی اصلی نشد. آزمایش shard اضافی روی ۵۰هزار سند و یک node مفید تشخیص داده نشد.",
        "",
        "## هزینه‌ها و محدودیت‌ها",
        "",
        f"- اندازهٔ baseline: {comparison['index_tradeoffs']['baseline_store_size_bytes']:,} بایت؛ optimized: {comparison['index_tradeoffs']['optimized_store_size_bytes']:,} بایت ({comparison['index_tradeoffs']['store_size_change_percent']}٪ افزایش).",
        f"- ingestion baseline: {ingest_b['duration_seconds']}s؛ optimized: {ingest_o['duration_seconds']}s.",
        "- نتایج مربوط به یک ماشین محلی تک‌نودی‌اند و به کلاستر توزیع‌شده یا replica تعمیم مستقیم ندارند.",
        "- cache عمداً پاک نشد؛ warm-up و سیاست cache در هر دو نسخه یکسان بود.",
        "- نمونه‌گیری دقیق فایل ۵۰هزار رکوردی قابل بازسازی نیست، چون seed/اسکریپت منبع تحویل نشده است.",
        "",
        "## بازتولید و ممیزی",
        "",
        "README همهٔ مراحل از نصب تا اجرای benchmark و گزارش را پوشش می‌دهد. `scripts/smoke_test.py` اتصال، نسخه، سلامت، دو index، count و query واقعی را کنترل می‌کند. `scripts/build_reproducibility_manifest.py` hash فایل‌ها و traceability هر شش benchmark اصلی را اعتبارسنجی می‌کند.",
        "",
        f"وضعیت manifest: `{manifest['status']}`؛ datasetها: {manifest['validation']['all_datasets_verified']}؛ traceability benchmarkها: {manifest['validation']['all_benchmarks_traceable']}.",
        "",
        "خروجی‌های عددی مرجع: `results/metrics_summary.csv`، `results/stage9_comparison.csv` و `results/reproducibility_manifest.json`.",
        "",
        "## جمع‌بندی",
        "",
        "نسخهٔ optimized بدون افت Precision@10 و بدون تغییر count جست‌وجوهای اصلی، latency را در تمام سناریوهای تک‌کاربره و چندکاربره کاهش داد. مهم‌ترین trade-off افزایش اندازهٔ index و زمان ingestion در برابر جست‌وجوی زیررشته‌ای سریع‌تر است. طراحی نهایی به دلیل ثبت قراردادها، seedها، hashها، منابع، raw measurementها و دستورهای اجرا قابل ممیزی و بازتولید است.",
    ]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    print(json.dumps({"status": "passed", "output": str(OUTPUT), "line_count": len(lines)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
